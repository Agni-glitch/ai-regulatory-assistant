# ComplyNexus frontend — build the React SPA, serve it with nginx.

# --- Build stage ------------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# Disable strict SSL so npm works behind corporate TLS-interception proxies
# (e.g. Zscaler) that break certificate verification against the registry.
RUN npm config set strict-ssl false \
    && npm install
COPY . .
# VITE_API_BASE can be overridden at build time; defaults to /api.
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

# --- Runtime stage ----------------------------------------------------------
FROM nginx:1.27-alpine
# SPA-friendly config: fall back to index.html for client-side routing.
COPY nginx.spa.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
