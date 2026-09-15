FROM nginx:alpine

RUN apk add --no-cache python3 ca-certificates

COPY . /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY start.sh /start.sh
RUN chmod +x /start.sh \
  && rm -f /usr/share/nginx/html/.env

# The PDF is intentionally served as a static asset from the same origin.
# /api is reverse-proxied to the local DeepSeek chat server.
EXPOSE 80
CMD ["/start.sh"]
