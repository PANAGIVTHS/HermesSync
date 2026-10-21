# Hermes persistent tool environment.
# Sourced by Bash through BASH_ENV for agent terminal commands.
# Keep this file silent, quick, and safe to source repeatedly.
# This is POSIX-shell-compatible so it also works in login-shell hooks.

export HERMES_TOOLS_HOME="${HERMES_TOOLS_HOME:-/opt/hermes-tools}"

# Standalone binaries and wrapper scripts placed here are available on PATH.
case ":${PATH:-}:" in
    *":$HERMES_TOOLS_HOME/bin:"*) ;;
    *) PATH="$HERMES_TOOLS_HOME/bin:${PATH:-/usr/local/bin:/usr/bin:/bin}" ;;
esac
export PATH

# Reuse Gradle wrapper distributions and caches across all projects.
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$HERMES_TOOLS_HOME/caches/gradle}"

# Optional active Java version. Hermes can point 'jdks/current' at any JDK.
if [ -x "$HERMES_TOOLS_HOME/jdks/current/bin/java" ]; then
    export JAVA_HOME="$HERMES_TOOLS_HOME/jdks/current"
    case ":$PATH:" in
        *":$JAVA_HOME/bin:"*) ;;
        *) PATH="$JAVA_HOME/bin:$PATH" ;;
    esac
    export PATH
fi

# Additional per-tool environment definitions without Compose modifications.
# Each snippet must be non-interactive, quiet and safe to source repeatedly.
for _hermes_tool_env_file in "$HERMES_TOOLS_HOME"/env.d/*.sh; do
    if [ -r "$_hermes_tool_env_file" ]; then
        . "$_hermes_tool_env_file"
    fi
done
unset _hermes_tool_env_file
