CC=gcc
CFLAGS=-O2 -std=c17 -Wall -Wextra -Werror -pedantic -I.
LDFLAGS=`pkg-config --libs libusb-1.0`
LIBS=

SRC=main.c crc16.c ring.c util.c
OBJ=$(SRC:.c=.o)
BIN=usb_stream_demo

all: $(BIN)

$(BIN): $(OBJ)
	$(CC) $(OBJ) -lusb-1.0 -o $@

%.o: %.c %.h
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJ) $(BIN)

run: $(BIN)
	./$(BIN)

.PHONY: all clean run
