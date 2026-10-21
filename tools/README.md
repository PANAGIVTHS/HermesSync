# Hermes shared development tools

This directory is mounted at `/opt/hermes-tools` inside the Hermes Docker container.
It is **not** a project workspace and is **never** included by Hermes Sync.

For Hermes agents installing reusable tools:

- Put standalone programs / launcher scripts in `/opt/hermes-tools/bin/`.
- Put downloaded archives in `/opt/hermes-tools/downloads/`.
- Put Java installations in `/opt/hermes-tools/jdks/`, and point `jdks/current`
  at the desired JDK, e.g. `ln -sfn jdk-25 /opt/hermes-tools/jdks/current`.
  `JAVA_HOME` and Java `PATH` are then set automatically in new Bash shells.
- `./gradlew` downloads Gradle itself. Its cache and distributions are stored at
  `/opt/hermes-tools/caches/gradle/` through `GRADLE_USER_HOME`.
- If an application requires additional environment variables, create a small
  `/opt/hermes-tools/env.d/<tool>.sh` snippet. It must not print output or prompt,
  and should be safe to source more than once.
- Never place a copy of an unrelated project or secrets in this shared directory.
- Do not edit Docker Compose for individual tools. Source the persistent `env.sh`
  manually only if using a non-Bash environment; Hermes terminal Bash automatically
  sources it via `BASH_ENV`.

Use binaries designed for this container's Linux architecture and runtime libraries.
System packages installed inside the container are not guaranteed to persist when
Docker recreates the container. Prefer user-space distributions stored here.
