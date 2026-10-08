FROM node:22-alpine AS web
WORKDIR /src/control-web
COPY control-web/package*.json ./
RUN npm ci
COPY control-web/ ./
RUN npm run build

FROM golang:1.26-alpine AS server
WORKDIR /src/control-server
COPY control-server/go.mod control-server/go.sum ./
RUN go mod download
COPY control-server/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /ctl-server ./cmd/ctl-server

FROM alpine:3.22
RUN apk add --no-cache ca-certificates && addgroup -g 10001 ctl && adduser -D -H -u 10001 -G ctl ctl
COPY --from=server /ctl-server /usr/local/bin/ctl-server
COPY --from=web /src/control-web/dist /app/web
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 CMD wget -q -O /dev/null http://127.0.0.1:8080/api/v1/health/ready || exit 1
ENTRYPOINT ["ctl-server"]
