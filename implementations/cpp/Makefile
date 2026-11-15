CXX = c++
CXXFLAGS = -std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic

.PHONY: release clean
release: build/resample

build/resample: main.cpp Makefile
	mkdir -p build
	$(CXX) $(CXXFLAGS) main.cpp -o $@

clean:
	rm -f build/resample
