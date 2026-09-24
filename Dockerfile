FROM alpine:3.20
RUN apk add --no-cache curl coreutils
COPY relay.sh /relay.sh
RUN chmod +x /relay.sh
CMD ["/bin/sh","/relay.sh"]
