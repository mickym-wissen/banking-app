# ─── Build stage ─────────────────────────────────────────────────────────────
FROM maven:3.9.6-eclipse-temurin-17 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn dependency:go-offline -q
COPY src ./src
RUN mvn package -DskipTests -q

# ─── Runtime stage ───────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jre-jammy
WORKDIR /app

# Install mysql client so entrypoint.sh can truncate log_incidents on startup
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-mysql-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/target/banking-app-1.0.0.jar banking-app.jar
COPY entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

RUN mkdir -p /app/logs

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
