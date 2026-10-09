FROM nginx:1.28.2-alpine
COPY infra/nginx.conf /etc/nginx/nginx.conf
