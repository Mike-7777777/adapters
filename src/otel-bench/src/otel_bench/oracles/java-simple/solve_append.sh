
# Added by the Harbor adapter. The verifier builds and runs with a bare
# `javac Demo.java` and `java Demo`, so the OpenTelemetry classes have to be on the
# default classpath, which is the working directory.
for dependency in lib/*.jar; do
  jar xf "$dependency"
done
javac Demo.java
