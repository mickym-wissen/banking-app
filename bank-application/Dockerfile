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

COPY --from=build /app/target/banking-app-1.0.0.jar banking-app.jar

RUN mkdir -p /app/logs

EXPOSE 8080

ENTRYPOINT ["java", "-jar", "/app/banking-app.jar"]
