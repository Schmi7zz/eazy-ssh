package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/pkg/sftp"
	"golang.org/x/crypto/ssh"
)

// ─── Types ───

type ConnectRequest struct {
	Host       string `json:"host"`
	Port       int    `json:"port"`
	Username   string `json:"username"`
	AuthType   string `json:"auth_type"`
	Password   string `json:"password,omitempty"`
	PrivateKey string `json:"private_key,omitempty"`
	Passphrase string `json:"passphrase,omitempty"`
	InitData   string `json:"init_data"`
}

type WSMessage struct {
	Type string          `json:"type"`
	Data json.RawMessage `json:"data"`
}

type ResizeMsg struct {
	Cols int `json:"cols"`
	Rows int `json:"rows"`
}

type SFTPCommand struct {
	Action string `json:"action"` // list, download, upload, delete, mkdir, rename, stat
	Path   string `json:"path"`
	Dest   string `json:"dest,omitempty"`    // for rename
	Data   string `json:"data,omitempty"`    // base64 for upload
	Name   string `json:"name,omitempty"`    // filename for upload
}

type FileInfo struct {
	Name    string `json:"name"`
	Size    int64  `json:"size"`
	IsDir   bool   `json:"is_dir"`
	ModTime string `json:"mod_time"`
	Perms   string `json:"perms"`
}

var (
	upgrader = websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool { return true },
	}
	botToken string
)

// ─── Main ───

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	botToken = os.Getenv("BOT_TOKEN")
	if botToken == "" {
		log.Fatal("BOT_TOKEN environment variable is required")
	}

	allowedOrigin := os.Getenv("ALLOWED_ORIGIN")
	if allowedOrigin != "" {
		upgrader.CheckOrigin = func(r *http.Request) bool {
			return r.Header.Get("Origin") == allowedOrigin
		}
	}

	http.HandleFunc("/ws", handleWS)
	http.HandleFunc("/sftp", handleSFTP)
	http.HandleFunc("/sftp-download", handleSFTPDownloadHTTP)
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("ok"))
	})

	log.Printf("SSH WebSocket proxy listening on :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}

// ─── Auth helpers ───

func validateInitData(initData string) bool {
	parsed, err := url.ParseQuery(initData)
	if err != nil {
		return false
	}
	receivedHash := parsed.Get("hash")
	if receivedHash == "" {
		return false
	}
	parsed.Del("hash")
	var keys []string
	for k := range parsed {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var parts []string
	for _, k := range keys {
		parts = append(parts, k+"="+parsed.Get(k))
	}
	dataCheckString := strings.Join(parts, "\n")
	secretKey := hmacSHA256([]byte("WebAppData"), []byte(botToken))
	computedHash := hex.EncodeToString(hmacSHA256(secretKey, []byte(dataCheckString)))
	return hmac.Equal([]byte(computedHash), []byte(receivedHash))
}

func hmacSHA256(key, data []byte) []byte {
	h := hmac.New(sha256.New, key)
	h.Write(data)
	return h.Sum(nil)
}

func buildSSHConfig(req ConnectRequest) (*ssh.ClientConfig, error) {
	config := &ssh.ClientConfig{
		User:            req.Username,
		HostKeyCallback: ssh.InsecureIgnoreHostKey(),
		Timeout:         15 * time.Second,
	}

	switch req.AuthType {
	case "password":
		config.Auth = []ssh.AuthMethod{ssh.Password(req.Password)}
	case "key":
		var signer ssh.Signer
		var err error
		if req.Passphrase != "" {
			signer, err = ssh.ParsePrivateKeyWithPassphrase([]byte(req.PrivateKey), []byte(req.Passphrase))
		} else {
			signer, err = ssh.ParsePrivateKey([]byte(req.PrivateKey))
		}
		if err != nil {
			return nil, fmt.Errorf("invalid SSH key: %v", err)
		}
		config.Auth = []ssh.AuthMethod{ssh.PublicKeys(signer)}
	default:
		return nil, fmt.Errorf("auth_type must be 'password' or 'key'")
	}

	return config, nil
}

// ─── SSH Terminal Handler ───

func handleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("upgrade error: %v", err)
		return
	}
	defer conn.Close()

	_, msg, err := conn.ReadMessage()
	if err != nil {
		return
	}

	var req ConnectRequest
	if err := json.Unmarshal(msg, &req); err != nil {
		sendError(conn, "invalid connect request")
		return
	}

	if req.InitData == "" || !validateInitData(req.InitData) {
		sendError(conn, "unauthorized: open from Telegram Mini App")
		return
	}

	if req.Port == 0 {
		req.Port = 22
	}

	config, err := buildSSHConfig(req)
	if err != nil {
		sendError(conn, err.Error())
		return
	}

	addr := fmt.Sprintf("%s:%d", req.Host, req.Port)
	sshConn, err := ssh.Dial("tcp", addr, config)
	if err != nil {
		sendError(conn, fmt.Sprintf("SSH connection failed: %v", err))
		return
	}
	defer sshConn.Close()

	// SSH keepalive
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			_, _, err := sshConn.SendRequest("keepalive@openssh.com", true, nil)
			if err != nil {
				return
			}
		}
	}()

	session, err := sshConn.NewSession()
	if err != nil {
		sendError(conn, fmt.Sprintf("session error: %v", err))
		return
	}
	defer session.Close()

	modes := ssh.TerminalModes{
		ssh.ECHO:          1,
		ssh.TTY_OP_ISPEED: 14400,
		ssh.TTY_OP_OSPEED: 14400,
	}
	if err := session.RequestPty("xterm-256color", 24, 80, modes); err != nil {
		sendError(conn, fmt.Sprintf("pty error: %v", err))
		return
	}

	stdinPipe, _ := session.StdinPipe()
	stdoutPipe, _ := session.StdoutPipe()
	stderrPipe, _ := session.StderrPipe()

	if err := session.Shell(); err != nil {
		sendError(conn, fmt.Sprintf("shell error: %v", err))
		return
	}

	sendJSON(conn, map[string]string{"type": "connected"})

	// WebSocket idle timeout
	idleTimeout := 10 * time.Minute
	conn.SetReadDeadline(time.Now().Add(idleTimeout))
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(idleTimeout))
		return nil
	})

	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			if err := conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}()

	done := make(chan struct{})
	var closeOnce sync.Once
	closeDone := func() { closeOnce.Do(func() { close(done) }) }

	go func() {
		buf := make([]byte, 8192)
		for {
			n, err := stdoutPipe.Read(buf)
			if n > 0 {
				resp, _ := json.Marshal(map[string]interface{}{"type": "output", "data": string(buf[:n])})
				if conn.WriteMessage(websocket.TextMessage, resp) != nil {
					closeDone()
					return
				}
			}
			if err != nil {
				closeDone()
				return
			}
		}
	}()

	go func() {
		buf := make([]byte, 8192)
		for {
			n, err := stderrPipe.Read(buf)
			if n > 0 {
				resp, _ := json.Marshal(map[string]interface{}{"type": "output", "data": string(buf[:n])})
				conn.WriteMessage(websocket.TextMessage, resp)
			}
			if err != nil {
				return
			}
		}
	}()

	go func() {
		defer stdinPipe.Close()
		for {
			_, raw, err := conn.ReadMessage()
			if err != nil {
				closeDone()
				return
			}
			var wsMsg WSMessage
			if json.Unmarshal(raw, &wsMsg) != nil {
				stdinPipe.Write(raw)
				continue
			}
			switch wsMsg.Type {
			case "input":
				var input string
				json.Unmarshal(wsMsg.Data, &input)
				stdinPipe.Write([]byte(input))
			case "resize":
				var resize ResizeMsg
				json.Unmarshal(wsMsg.Data, &resize)
				session.WindowChange(resize.Rows, resize.Cols)
			case "disconnect":
				closeDone()
				return
			}
		}
	}()

	go func() {
		session.Wait()
		closeDone()
	}()

	<-done
	sendJSON(conn, map[string]string{"type": "disconnected"})
}

// ─── SFTP Handler ───

func handleSFTP(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("sftp upgrade error: %v", err)
		return
	}
	defer conn.Close()

	// First message: connection info
	_, msg, err := conn.ReadMessage()
	if err != nil {
		return
	}

	var req ConnectRequest
	if err := json.Unmarshal(msg, &req); err != nil {
		sendError(conn, "invalid connect request")
		return
	}

	if req.InitData == "" || !validateInitData(req.InitData) {
		sendError(conn, "unauthorized: open from Telegram Mini App")
		return
	}

	if req.Port == 0 {
		req.Port = 22
	}

	config, err := buildSSHConfig(req)
	if err != nil {
		sendError(conn, err.Error())
		return
	}

	addr := fmt.Sprintf("%s:%d", req.Host, req.Port)
	sshConn, err := ssh.Dial("tcp", addr, config)
	if err != nil {
		sendError(conn, fmt.Sprintf("SFTP connection failed: %v", err))
		return
	}
	defer sshConn.Close()

	sftpClient, err := sftp.NewClient(sshConn)
	if err != nil {
		sendError(conn, fmt.Sprintf("SFTP client error: %v", err))
		return
	}
	defer sftpClient.Close()

	sendJSON(conn, map[string]string{"type": "connected"})

	// Keepalive
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			_, _, err := sshConn.SendRequest("keepalive@openssh.com", true, nil)
			if err != nil {
				return
			}
		}
	}()

	// Handle SFTP commands
	for {
		_, raw, err := conn.ReadMessage()
		if err != nil {
			return
		}

		var cmd SFTPCommand
		if err := json.Unmarshal(raw, &cmd); err != nil {
			sendError(conn, "invalid command")
			continue
		}

		switch cmd.Action {
		case "list":
			handleSFTPList(conn, sftpClient, cmd.Path)
		case "download":
			handleSFTPDownload(conn, sftpClient, cmd.Path)
		case "upload":
			handleSFTPUpload(conn, sftpClient, cmd.Path, cmd.Name, cmd.Data)
		case "delete":
			handleSFTPDelete(conn, sftpClient, cmd.Path)
		case "mkdir":
			handleSFTPMkdir(conn, sftpClient, cmd.Path)
		case "rename":
			handleSFTPRename(conn, sftpClient, cmd.Path, cmd.Dest)
		case "stat":
			handleSFTPStat(conn, sftpClient, cmd.Path)
		case "disconnect":
			return
		default:
			sendError(conn, "unknown action: "+cmd.Action)
		}
	}
}

func handleSFTPList(conn *websocket.Conn, client *sftp.Client, path string) {
	if path == "" {
		path = "."
	}

	// Resolve home dir
	if path == "." || path == "~" {
		wd, err := client.Getwd()
		if err == nil {
			path = wd
		} else {
			path = "/"
		}
	}

	entries, err := client.ReadDir(path)
	if err != nil {
		sendError(conn, fmt.Sprintf("list error: %v", err))
		return
	}

	files := make([]FileInfo, 0, len(entries))
	for _, e := range entries {
		files = append(files, FileInfo{
			Name:    e.Name(),
			Size:    e.Size(),
			IsDir:   e.IsDir(),
			ModTime: e.ModTime().Format("2006-01-02 15:04"),
			Perms:   e.Mode().String(),
		})
	}

	resp, _ := json.Marshal(map[string]interface{}{
		"type": "list",
		"path": path,
		"data": files,
	})
	conn.WriteMessage(websocket.TextMessage, resp)
}

func handleSFTPDownload(conn *websocket.Conn, client *sftp.Client, path string) {
	file, err := client.Open(path)
	if err != nil {
		sendError(conn, fmt.Sprintf("download error: %v", err))
		return
	}
	defer file.Close()

	stat, err := file.Stat()
	if err != nil {
		sendError(conn, fmt.Sprintf("stat error: %v", err))
		return
	}

	// Max 50MB
	if stat.Size() > 50*1024*1024 {
		sendError(conn, "file too large (max 50MB)")
		return
	}

	data, err := io.ReadAll(file)
	if err != nil {
		sendError(conn, fmt.Sprintf("read error: %v", err))
		return
	}

	encoded := base64.StdEncoding.EncodeToString(data)

	resp, _ := json.Marshal(map[string]interface{}{
		"type": "download",
		"name": filepath.Base(path),
		"size": stat.Size(),
		"data": encoded,
	})
	conn.WriteMessage(websocket.TextMessage, resp)
}

func handleSFTPUpload(conn *websocket.Conn, client *sftp.Client, path, name, dataB64 string) {
	data, err := base64.StdEncoding.DecodeString(dataB64)
	if err != nil {
		sendError(conn, "invalid base64 data")
		return
	}

	fullPath := filepath.Join(path, name)
	file, err := client.Create(fullPath)
	if err != nil {
		sendError(conn, fmt.Sprintf("upload error: %v", err))
		return
	}
	defer file.Close()

	_, err = file.Write(data)
	if err != nil {
		sendError(conn, fmt.Sprintf("write error: %v", err))
		return
	}

	resp, _ := json.Marshal(map[string]string{
		"type":    "upload_ok",
		"message": fmt.Sprintf("Uploaded %s (%d bytes)", name, len(data)),
	})
	conn.WriteMessage(websocket.TextMessage, resp)
}

func handleSFTPDelete(conn *websocket.Conn, client *sftp.Client, path string) {
	info, err := client.Stat(path)
	if err != nil {
		sendError(conn, fmt.Sprintf("stat error: %v", err))
		return
	}

	if info.IsDir() {
		err = removeDirRecursive(client, path)
	} else {
		err = client.Remove(path)
	}

	if err != nil {
		sendError(conn, fmt.Sprintf("delete error: %v", err))
		return
	}

	resp, _ := json.Marshal(map[string]string{
		"type":    "delete_ok",
		"message": "Deleted: " + filepath.Base(path),
	})
	conn.WriteMessage(websocket.TextMessage, resp)
}

func removeDirRecursive(client *sftp.Client, path string) error {
	entries, err := client.ReadDir(path)
	if err != nil {
		return err
	}
	for _, e := range entries {
		fullPath := filepath.Join(path, e.Name())
		if e.IsDir() {
			if err := removeDirRecursive(client, fullPath); err != nil {
				return err
			}
		} else {
			if err := client.Remove(fullPath); err != nil {
				return err
			}
		}
	}
	return client.RemoveDirectory(path)
}

func handleSFTPMkdir(conn *websocket.Conn, client *sftp.Client, path string) {
	err := client.MkdirAll(path)
	if err != nil {
		sendError(conn, fmt.Sprintf("mkdir error: %v", err))
		return
	}

	resp, _ := json.Marshal(map[string]string{
		"type":    "mkdir_ok",
		"message": "Created: " + filepath.Base(path),
	})
	conn.WriteMessage(websocket.TextMessage, resp)
}

func handleSFTPRename(conn *websocket.Conn, client *sftp.Client, oldPath, newPath string) {
	err := client.Rename(oldPath, newPath)
	if err != nil {
		sendError(conn, fmt.Sprintf("rename error: %v", err))
		return
	}

	resp, _ := json.Marshal(map[string]string{
		"type":    "rename_ok",
		"message": "Renamed",
	})
	conn.WriteMessage(websocket.TextMessage, resp)
}

func handleSFTPStat(conn *websocket.Conn, client *sftp.Client, path string) {
	info, err := client.Stat(path)
	if err != nil {
		sendError(conn, fmt.Sprintf("stat error: %v", err))
		return
	}

	resp, _ := json.Marshal(map[string]interface{}{
		"type": "stat",
		"data": FileInfo{
			Name:    info.Name(),
			Size:    info.Size(),
			IsDir:   info.IsDir(),
			ModTime: info.ModTime().Format("2006-01-02 15:04"),
			Perms:   info.Mode().String(),
		},
	})
	conn.WriteMessage(websocket.TextMessage, resp)
}

// ─── Helpers ───

func sendError(conn *websocket.Conn, msg string) {
	resp, _ := json.Marshal(map[string]string{"type": "error", "data": msg})
	conn.WriteMessage(websocket.TextMessage, resp)
}

func sendJSON(conn *websocket.Conn, v interface{}) {
	data, _ := json.Marshal(v)
	conn.WriteMessage(websocket.TextMessage, data)
}

// ─── SFTP Download via Telegram Bot ───

type DownloadRequest struct {
	Host       string `json:"host"`
	Port       int    `json:"port"`
	Username   string `json:"username"`
	AuthType   string `json:"auth_type"`
	Password   string `json:"password,omitempty"`
	PrivateKey string `json:"private_key,omitempty"`
	Passphrase string `json:"passphrase,omitempty"`
	InitData   string `json:"init_data"`
	Path       string `json:"path"`
	ChatID     int64  `json:"chat_id"`
}

func handleSFTPDownloadHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
	if r.Method == "OPTIONS" {
		w.WriteHeader(200)
		return
	}
	if r.Method != "POST" {
		http.Error(w, "POST only", 405)
		return
	}

	var req DownloadRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonResp(w, 400, map[string]string{"error": "invalid request"})
		return
	}

	if req.InitData == "" || !validateInitData(req.InitData) {
		jsonResp(w, 401, map[string]string{"error": "unauthorized"})
		return
	}

	if req.Port == 0 {
		req.Port = 22
	}

	config, err := buildSSHConfig(ConnectRequest{
		Host: req.Host, Port: req.Port, Username: req.Username,
		AuthType: req.AuthType, Password: req.Password,
		PrivateKey: req.PrivateKey, Passphrase: req.Passphrase,
	})
	if err != nil {
		jsonResp(w, 400, map[string]string{"error": err.Error()})
		return
	}

	addr := fmt.Sprintf("%s:%d", req.Host, req.Port)
	sshConn, err := ssh.Dial("tcp", addr, config)
	if err != nil {
		jsonResp(w, 500, map[string]string{"error": "SSH failed: " + err.Error()})
		return
	}
	defer sshConn.Close()

	sftpClient, err := sftp.NewClient(sshConn)
	if err != nil {
		jsonResp(w, 500, map[string]string{"error": "SFTP failed: " + err.Error()})
		return
	}
	defer sftpClient.Close()

	file, err := sftpClient.Open(req.Path)
	if err != nil {
		jsonResp(w, 500, map[string]string{"error": "File not found: " + err.Error()})
		return
	}
	defer file.Close()

	stat, _ := file.Stat()
	if stat.Size() > 50*1024*1024 {
		jsonResp(w, 400, map[string]string{"error": "File too large (max 50MB)"})
		return
	}

	fileData, err := io.ReadAll(file)
	if err != nil {
		jsonResp(w, 500, map[string]string{"error": "Read error: " + err.Error()})
		return
	}

	fileName := filepath.Base(req.Path)

	// Send via Telegram Bot API
	err = sendFileViaTelegram(req.ChatID, fileName, fileData)
	if err != nil {
		jsonResp(w, 500, map[string]string{"error": "Telegram send failed: " + err.Error()})
		return
	}

	jsonResp(w, 200, map[string]string{"status": "sent", "name": fileName})
}

func sendFileViaTelegram(chatID int64, fileName string, data []byte) error {
	apiURL := fmt.Sprintf("https://api.telegram.org/bot%s/sendDocument", botToken)

	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)

	_ = writer.WriteField("chat_id", fmt.Sprintf("%d", chatID))
	_ = writer.WriteField("caption", "📂 "+fileName)

	part, err := writer.CreateFormFile("document", fileName)
	if err != nil {
		return err
	}
	_, err = part.Write(data)
	if err != nil {
		return err
	}
	writer.Close()

	resp, err := http.Post(apiURL, writer.FormDataContentType(), &buf)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("telegram API error: %s", string(body))
	}

	return nil
}

func jsonResp(w http.ResponseWriter, code int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(data)
}
