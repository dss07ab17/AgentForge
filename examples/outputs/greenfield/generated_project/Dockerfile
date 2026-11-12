FROM python:3.12-slim
WORKDIR /app
COPY shortener ./shortener
ENV SHORTENER_DATABASE_PATH=/data/shortener.db
VOLUME /data
EXPOSE 8080
USER 65534
CMD ["python", "-m", "shortener.server", "--host", "0.0.0.0", "--port", "8080"]
