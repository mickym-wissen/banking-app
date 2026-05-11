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

# Download the Datadog Java APM agent
ADD https://dtdg.co/latest-java-tracer dd-java-agent.jar

COPY --from=build /app/target/banking-app-1.0.0.jar banking-app.jar

# Create log directory (mount to host / ship via Datadog agent)
RUN mkdir -p /app/logs

EXPOSE 8080

# ── Datadog Unified Service Tagging ──
ENV DD_SERVICE=banking-app \
    DD_ENV=production \
    DD_VERSION=1.0.0 \
    DD_LOGS_INJECTION=true \
    DD_TRACE_SAMPLE_RATE=1 \
    DD_PROFILING_ENABLED=true \
    DD_APPSEC_ENABLED=false

ENTRYPOINT ["java", \
  "-javaagent:/app/dd-java-agent.jar", \
  "-Ddd.service=${DD_SERVICE}", \
  "-Ddd.env=${DD_ENV}", \
  "-Ddd.version=${DD_VERSION}", \
  "-Ddd.logs.injection=${DD_LOGS_INJECTION}", \
  "-Ddd.trace.sample.rate=${DD_TRACE_SAMPLE_RATE}", \
  "-Ddd.profiling.enabled=${DD_PROFILING_ENABLED}", \
  "-jar", "/app/banking-app.jar"]
