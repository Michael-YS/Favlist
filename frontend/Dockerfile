# Multi-stage production image for the Favlist React single-page application.
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build

FROM nginx:1.27-alpine

ENV TRUSTED_EDGE_PROXY=172.30.55.1 \
    NGINX_ENVSUBST_FILTER=TRUSTED_EDGE_PROXY

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
