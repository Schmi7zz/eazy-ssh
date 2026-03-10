FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod ./
RUN go mod download
COPY . .
RUN go mod tidy
RUN CGO_ENABLED=0 go build -o ssh-proxy .

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/ssh-proxy /usr/local/bin/
EXPOSE 8080
CMD ["ssh-proxy"]
