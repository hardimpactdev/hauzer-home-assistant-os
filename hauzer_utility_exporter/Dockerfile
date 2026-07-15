FROM ghcr.io/home-assistant/base:3.23

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=0.1.0

ENV HAUZER_EXPORTER_VERSION="${BUILD_VERSION}"

RUN apk add --no-cache ca-certificates py3-pip python3 \
    && pip3 install --no-cache-dir --break-system-packages websocket-client==1.8.0

COPY rootfs /

RUN update-ca-certificates

LABEL \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.name="Hauzer Utility Exporter" \
    io.hass.description="Push Home Assistant utility statistics to Hauzer" \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION}" \
    org.opencontainers.image.title="Home Assistant App: Hauzer Utility Exporter" \
    org.opencontainers.image.description="Push Home Assistant utility statistics to Hauzer" \
    org.opencontainers.image.source="https://github.com/hardimpactdev/hauzer-home-assistant-os" \
    org.opencontainers.image.url="https://hauzer.app" \
    org.opencontainers.image.licenses="MIT"
