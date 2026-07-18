/* FogGBA Wi-Fi Receive — FogTransfer v1 server
 *
 * Protocol:
 *   Server -> Client: "FOGGBA 1\n"
 *   Client -> Server: "PUT <filename>\n"
 *   Client -> Server: "SIZE <bytes>\n"
 *   Server -> Client: "READY\n" | "ERR ...\n"
 *   Client -> Server: <raw bytes>
 *   Server -> Client: "OK\n" | "ERR ...\n"
 */

#include "common.h"
#include "gui.h"
#include "video.h"
#include "mh_print.h"
#include "input.h"
#include "wifi_receive.h"

#include <pspctrl.h>
#include <pspkernel.h>
#include <psputility.h>
#include <pspnet.h>
#include <pspnet_inet.h>
#include <pspnet_apctl.h>

#include <sys/socket.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>

#define FOG_PORT            2121
#define FOG_MAX_FILE_SIZE   (32 * 1024 * 1024)
#define FOG_CHUNK           (16 * 1024)
#define FOG_CONNECT_TIMEOUT 60

#define WR_COLOR_BG       COLOR15(8, 15, 12)
#define WR_COLOR_ACTIVE   COLOR15(31, 31, 29)
#define WR_COLOR_INACTIVE COLOR15(12, 25, 18)
#define WR_COLOR_HELP     COLOR15(20, 28, 22)

extern u32 option_button_mapping;

enum
{
  WR_IDLE = 0,
  WR_WAITING,
  WR_RECEIVING,
  WR_DONE,
  WR_ERROR
};

static int g_net_inited = 0;
static char g_status[160];
static char g_detail[160];
static int g_state = WR_IDLE;
static int g_progress = 0;

static void wr_set_status(const char *s)
{
  strncpy(g_status, s, sizeof(g_status) - 1);
  g_status[sizeof(g_status) - 1] = 0;
}

static void wr_set_detail(const char *s)
{
  strncpy(g_detail, s, sizeof(g_detail) - 1);
  g_detail[sizeof(g_detail) - 1] = 0;
}

static int cancel_pressed(void)
{
  u32 pad = get_pad_input(0x0001FFFF);
  /* option_button_mapping: 0 = X confirm / O cancel; 1 = swapped */
  if (option_button_mapping == 0)
    return (pad & PSP_CTRL_CIRCLE) != 0;
  return (pad & PSP_CTRL_CROSS) != 0;
}

static void wr_draw(void)
{
  char line[96];

  clear_screen(COLOR15_TO_32(WR_COLOR_BG));
  print_string("Fog Wi-Fi Receive", 18, 16, WR_COLOR_HELP, BG_NO_FILL);
  print_string(g_status, 18, 48, WR_COLOR_ACTIVE, BG_NO_FILL);
  if (g_detail[0])
    print_string(g_detail, 18, 68, WR_COLOR_INACTIVE, BG_NO_FILL);

  if (g_state == WR_RECEIVING)
  {
    sprintf(line, "Progress: %d%%", g_progress);
    print_string(line, 18, 100, WR_COLOR_ACTIVE, BG_NO_FILL);
  }

  print_string("Cancel button: Exit", 18, 250, WR_COLOR_HELP, BG_NO_FILL);
  flip_screen(1);
}

static int net_load_modules(void)
{
  int err;

  err = sceUtilityLoadNetModule(PSP_NET_MODULE_COMMON);
  if (err < 0 && err != 0x80110800) /* already loaded */
    return err;

  err = sceUtilityLoadNetModule(PSP_NET_MODULE_INET);
  if (err < 0 && err != 0x80110800)
    return err;

  return 0;
}

static void net_unload_modules(void)
{
  sceUtilityUnloadNetModule(PSP_NET_MODULE_INET);
  sceUtilityUnloadNetModule(PSP_NET_MODULE_COMMON);
}

static int net_stack_init(void)
{
  int err;

  err = sceNetInit(128 * 1024, 42, 4 * 1024, 42, 4 * 1024);
  if (err < 0)
    return err;

  err = sceNetInetInit();
  if (err < 0)
    return err;

  err = sceNetApctlInit(0x8000, 48);
  if (err < 0)
    return err;

  g_net_inited = 1;
  return 0;
}

static void net_stack_term(void)
{
  if (!g_net_inited)
    return;

  sceNetApctlTerm();
  sceNetInetTerm();
  sceNetTerm();
  g_net_inited = 0;
}

static int net_wait_ip(char *ip_out, int ip_out_len)
{
  int state = 0;
  int err;
  int tries;
  union SceNetApctlInfo info;

  /* Already connected? */
  err = sceNetApctlGetState(&state);
  if (err >= 0 && state == PSP_NET_APCTL_STATE_GOT_IP)
  {
    memset(&info, 0, sizeof(info));
    if (sceNetApctlGetInfo(PSP_NET_APCTL_INFO_IP, &info) >= 0)
    {
      strncpy(ip_out, info.ip, ip_out_len - 1);
      ip_out[ip_out_len - 1] = 0;
      return 0;
    }
  }

  wr_set_status("Connecting to Wi-Fi...");
  wr_set_detail("Use a profile from XMB Network Settings");
  wr_draw();

  /* Try first few connection profiles */
  for (tries = 1; tries <= 5; tries++)
  {
    err = sceNetApctlConnect(tries);
    if (err < 0)
      continue;

    {
      int wait;
      for (wait = 0; wait < FOG_CONNECT_TIMEOUT * 20; wait++)
      {
        if (cancel_pressed())
        {
          sceNetApctlDisconnect();
          return -2;
        }

        err = sceNetApctlGetState(&state);
        if (err >= 0 && state == PSP_NET_APCTL_STATE_GOT_IP)
        {
          memset(&info, 0, sizeof(info));
          if (sceNetApctlGetInfo(PSP_NET_APCTL_INFO_IP, &info) >= 0)
          {
            strncpy(ip_out, info.ip, ip_out_len - 1);
            ip_out[ip_out_len - 1] = 0;
            return 0;
          }
        }

        if (err >= 0 && state == PSP_NET_APCTL_STATE_DISCONNECTED && wait > 40)
          break;

        sceDisplayWaitVblankStart();
        if ((wait % 10) == 0)
          wr_draw();
      }
    }

    sceNetApctlDisconnect();
  }

  return -1;
}

static int set_nonblock(int fd, int enable)
{
  int flags = fcntl(fd, F_GETFL, 0);
  if (flags < 0)
    return -1;
  if (enable)
    flags |= O_NONBLOCK;
  else
    flags &= ~O_NONBLOCK;
  return fcntl(fd, F_SETFL, flags);
}

static int sock_send_all(int fd, const void *buf, int len)
{
  const char *p = (const char *)buf;
  int sent = 0;

  while (sent < len)
  {
    int n = send(fd, p + sent, len - sent, 0);
    if (n < 0)
    {
      if (errno == EAGAIN || errno == EWOULDBLOCK)
      {
        sceKernelDelayThread(1000);
        continue;
      }
      return -1;
    }
    if (n == 0)
      return -1;
    sent += n;
  }
  return 0;
}

static int sock_recv_line(int fd, char *buf, int buflen, int timeout_ms)
{
  int pos = 0;
  int elapsed = 0;

  while (pos < buflen - 1)
  {
    char c;
    int n = recv(fd, &c, 1, 0);
    if (n < 0)
    {
      if (errno == EAGAIN || errno == EWOULDBLOCK)
      {
        if (cancel_pressed())
          return -2;
        sceKernelDelayThread(5000);
        elapsed += 5;
        if (elapsed > timeout_ms)
          return -1;
        continue;
      }
      return -1;
    }
    if (n == 0)
      return -1;
    if (c == '\n')
    {
      buf[pos] = 0;
      if (pos > 0 && buf[pos - 1] == '\r')
        buf[pos - 1] = 0;
      return pos;
    }
    buf[pos++] = c;
    elapsed = 0;
  }
  return -1;
}

static int valid_rom_name(const char *name)
{
  const char *ext;
  int len;

  if (name == NULL || name[0] == 0)
    return 0;
  if (strchr(name, '/') || strchr(name, '\\') || strstr(name, ".."))
    return 0;

  len = (int)strlen(name);
  if (len < 5 || len >= MAX_FILE)
    return 0;

  ext = strrchr(name, '.');
  if (ext == NULL)
    return 0;
  if (strcasecmp(ext, ".gba") == 0 || strcasecmp(ext, ".zip") == 0)
    return 1;
  return 0;
}

static int handle_client(int client, const char *rom_dir)
{
  char line[300];
  char filename[MAX_FILE];
  char path[MAX_PATH];
  char errbuf[128];
  long size = 0;
  long received = 0;
  SceUID fd = -1;
  char *chunk = NULL;
  int ok = -1;

  path[0] = 0;
  set_nonblock(client, 1);

  if (sock_send_all(client, "FOGGBA 1\n", 9) < 0)
    goto done;

  if (sock_recv_line(client, line, sizeof(line), 30000) < 0)
    goto done;

  if (strncmp(line, "PUT ", 4) != 0)
  {
    sock_send_all(client, "ERR bad PUT\n", 12);
    goto done;
  }

  strncpy(filename, line + 4, sizeof(filename) - 1);
  filename[sizeof(filename) - 1] = 0;

  if (!valid_rom_name(filename))
  {
    sock_send_all(client, "ERR bad name (use .gba/.zip)\n", 29);
    wr_set_detail("Rejected: bad filename");
    g_state = WR_ERROR;
    goto done;
  }

  if (sock_recv_line(client, line, sizeof(line), 30000) < 0)
    goto done;

  if (strncmp(line, "SIZE ", 5) != 0)
  {
    sock_send_all(client, "ERR bad SIZE\n", 13);
    goto done;
  }

  size = strtol(line + 5, NULL, 10);
  if (size <= 0 || size > FOG_MAX_FILE_SIZE)
  {
    sprintf(errbuf, "ERR size (max %d)\n", FOG_MAX_FILE_SIZE);
    sock_send_all(client, errbuf, (int)strlen(errbuf));
    wr_set_detail("Rejected: file too large");
    g_state = WR_ERROR;
    goto done;
  }

  sprintf(path, "%s%s", rom_dir, filename);
  fd = sceIoOpen(path, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
  if (fd < 0)
  {
    sock_send_all(client, "ERR cannot write\n", 17);
    wr_set_detail("Cannot open file on Memory Stick");
    g_state = WR_ERROR;
    goto done;
  }

  if (sock_send_all(client, "READY\n", 6) < 0)
    goto done;

  chunk = (char *)malloc(FOG_CHUNK);
  if (chunk == NULL)
  {
    sock_send_all(client, "ERR oom\n", 8);
    goto done;
  }

  g_state = WR_RECEIVING;
  g_progress = 0;
  sprintf(g_status, "Receiving %s", filename);
  wr_set_detail("");
  wr_draw();

  while (received < size)
  {
    int want = (int)((size - received) > FOG_CHUNK ? FOG_CHUNK : (size - received));
    int n;

    if (cancel_pressed())
    {
      sock_send_all(client, "ERR cancelled\n", 14);
      goto done;
    }

    n = recv(client, chunk, want, 0);
    if (n < 0)
    {
      if (errno == EAGAIN || errno == EWOULDBLOCK)
      {
        sceKernelDelayThread(1000);
        continue;
      }
      goto done;
    }
    if (n == 0)
      goto done;

    if (sceIoWrite(fd, chunk, n) != n)
    {
      sock_send_all(client, "ERR write failed\n", 17);
      goto done;
    }

    received += n;
    g_progress = (int)((received * 100) / size);
    if ((received & 0xFFFF) == 0 || received == size)
      wr_draw();
  }

  sock_send_all(client, "OK\n", 3);
  sprintf(g_status, "Saved: %s", filename);
  wr_set_detail("Press cancel to return");
  g_state = WR_DONE;
  g_progress = 100;
  ok = 0;

done:
  if (chunk)
    free(chunk);
  if (fd >= 0)
  {
    sceIoClose(fd);
    if (ok != 0 && path[0])
      sceIoRemove(path);
  }
  return ok;
}

void wifi_receive_run(void)
{
  char ip[32];
  int listen_fd = -1;
  int client_fd = -1;
  struct sockaddr_in addr;
  socklen_t addrlen;
  int err;

  g_state = WR_IDLE;
  g_progress = 0;
  g_status[0] = 0;
  g_detail[0] = 0;

  wr_set_status("Loading network...");
  wr_draw();

  err = net_load_modules();
  if (err < 0)
  {
    wr_set_status("Failed to load net modules");
    sprintf(g_detail, "Error %08X", err);
    g_state = WR_ERROR;
    goto ui_wait_exit;
  }

  err = net_stack_init();
  if (err < 0)
  {
    wr_set_status("Network init failed");
    sprintf(g_detail, "Error %08X", err);
    g_state = WR_ERROR;
    goto ui_cleanup;
  }

  err = net_wait_ip(ip, sizeof(ip));
  if (err == -2)
  {
    wr_set_status("Cancelled");
    g_state = WR_ERROR;
    goto ui_cleanup;
  }
  if (err < 0)
  {
    wr_set_status("No Wi-Fi IP");
    wr_set_detail("Connect PSP to Wi-Fi in XMB, then retry");
    g_state = WR_ERROR;
    goto ui_cleanup;
  }

  listen_fd = socket(AF_INET, SOCK_STREAM, 0);
  if (listen_fd < 0)
  {
    wr_set_status("socket() failed");
    g_state = WR_ERROR;
    goto ui_cleanup;
  }

  {
    int yes = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
  }

  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons(FOG_PORT);
  addr.sin_addr.s_addr = htonl(INADDR_ANY);

  if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0)
  {
    wr_set_status("bind() failed");
    g_state = WR_ERROR;
    goto ui_cleanup;
  }

  if (listen(listen_fd, 1) < 0)
  {
    wr_set_status("listen() failed");
    g_state = WR_ERROR;
    goto ui_cleanup;
  }

  set_nonblock(listen_fd, 1);

  sprintf(g_status, "IP: %s  Port: %d", ip, FOG_PORT);
  wr_set_detail("Waiting for PC (FogTransfer)...");
  g_state = WR_WAITING;
  wr_draw();

  while (g_state == WR_WAITING)
  {
    if (cancel_pressed())
      goto ui_cleanup;

    addrlen = sizeof(addr);
    client_fd = accept(listen_fd, (struct sockaddr *)&addr, &addrlen);
    if (client_fd >= 0)
    {
      wr_set_detail("PC connected...");
      wr_draw();
      handle_client(client_fd, dir_roms);
      close(client_fd);
      client_fd = -1;

      if (g_state == WR_DONE)
        break;

      /* After error, keep listening unless user cancels */
      if (g_state != WR_DONE)
      {
        g_state = WR_WAITING;
        sprintf(g_status, "IP: %s  Port: %d", ip, FOG_PORT);
        wr_set_detail("Waiting for PC (FogTransfer)...");
      }
    }
    else if (errno != EAGAIN && errno != EWOULDBLOCK)
    {
      wr_set_status("accept() error");
      g_state = WR_ERROR;
      break;
    }

    sceDisplayWaitVblankStart();
    wr_draw();
  }

ui_wait_exit:
  wr_draw();
  while (!cancel_pressed())
  {
    sceDisplayWaitVblankStart();
    wr_draw();
  }
  while (get_pad_input(0x0001FFFF) != 0)
    sceDisplayWaitVblankStart();

ui_cleanup:
  if (client_fd >= 0)
    close(client_fd);
  if (listen_fd >= 0)
    close(listen_fd);

  sceNetApctlDisconnect();
  net_stack_term();
  net_unload_modules();
}
