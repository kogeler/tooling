FROM golang:1.25-alpine AS builder

WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .

ENV CGO_ENABLED=0 GOOS=linux
RUN go test ./...
RUN go build -trimpath -ldflags="-s -w" -o /out/sms-to-telegram .

FROM alpine:latest

RUN addgroup -S app && adduser -S app -G app && apk add --no-cache ca-certificates

USER app
WORKDIR /app

COPY --from=builder /out/sms-to-telegram /usr/local/bin/sms-to-telegram

ENTRYPOINT ["/usr/local/bin/sms-to-telegram"]
