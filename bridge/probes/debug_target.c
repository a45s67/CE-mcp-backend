/* Cooperative x86/x64 debugger target. It mutates only its own global value. */
#include <stdint.h>
#include <stdio.h>
#include <windows.h>

static volatile uint8_t watched_buffer[4096] = {0};

int main(int argc, char **argv) {
  FILE *stream;
  DWORD deadline;
  if (argc != 2) return 2;
  stream = fopen(argv[1], "wb");
  if (!stream) return 3;
  fprintf(stream, "pid=%lu\naddress=%llX\n", (unsigned long)GetCurrentProcessId(),
          (unsigned long long)(uintptr_t)&watched_buffer[0]);
  fclose(stream);
  deadline = GetTickCount() + 120000;
  while ((int32_t)(deadline - GetTickCount()) > 0) {
    (*(volatile uint32_t *)&watched_buffer[0])++;
    Sleep(20);
  }
  return 0;
}
