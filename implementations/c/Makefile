CC = cc
CFLAGS = -O3 -DNDEBUG -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow
LDLIBS = -lm

.PHONY: all clean check
all: resample

resample: main.c
	$(CC) $(CFLAGS) main.c -o $@ $(LDLIBS)

check: resample
	python3 validate.py ./resample

clean:
	rm -f resample
