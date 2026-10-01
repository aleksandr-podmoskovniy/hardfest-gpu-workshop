FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /bridge .
FROM alpine:3.23
RUN apk add --no-cache ca-certificates
COPY --from=build /bridge /bridge
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/bridge"]
