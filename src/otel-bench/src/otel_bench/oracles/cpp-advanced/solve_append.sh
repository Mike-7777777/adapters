
# Added by the Harbor adapter. app.cpp prints from several threads with chained <<,
# so pieces of different lines interleave and the verifier's substring checks on the
# console output fail in a fraction of runs. Each statement now builds its line in a
# temporary and writes it under a lock in one call.
cd /workdir
sed -i 's/std::cout << /AtomicLine() << /' app.cpp
cat > /tmp/atomic_line.hpp <<'CPP'

// One console line per write, so output from concurrent threads cannot interleave.
struct AtomicLine {
    std::ostringstream line;
    ~AtomicLine() {
        static std::mutex console_mutex;
        std::lock_guard<std::mutex> lock(console_mutex);
        std::cout << line.str() << std::flush;
    }
    template <typename T>
    AtomicLine& operator<<(const T& value) {
        line << value;
        return *this;
    }
    AtomicLine& operator<<(std::ostream& (*manipulator)(std::ostream&)) {
        line << manipulator;
        return *this;
    }
};
CPP
sed -i '/^#include <variant>$/r /tmp/atomic_line.hpp' app.cpp
if [ "$(grep -c 'AtomicLine() << ' app.cpp)" -ne 7 ] || ! grep -q '^struct AtomicLine' app.cpp; then
  echo "app.cpp is not the file this fix was written for" >&2
  exit 1
fi
g++ -std=c++17 -o app app.cpp -pthread
