FROM golang:1.25.12-alpine3.23 AS build

WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/lucien ./

FROM alpine:3.23.5
RUN apk add --no-cache ca-certificates \
    && adduser -D -u 10001 -h /home/lucien lucien
COPY --from=build /out/lucien /usr/local/bin/lucien
USER 10001:10001
WORKDIR /home/lucien
ENTRYPOINT ["lucien"]
