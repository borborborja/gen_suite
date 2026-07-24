# Frontend image — Vite build served by nginx, which also reverse-proxies /api → backend.
#
# La etapa de build corre SIEMPRE en la arquitectura del runner (--platform=$BUILDPLATFORM):
# su salida son ficheros estáticos, iguales para amd64 y arm64. Sin esto, el build arm64
# multi-arch ejecuta node bajo QEMU y se cuelga durante horas.
FROM --platform=$BUILDPLATFORM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY docker/frontend-nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
