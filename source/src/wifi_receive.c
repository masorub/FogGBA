/* FogGBA Wi-Fi Receive — FogTransfer v1 server
 *
 * Netconf dialog matched to CM File Manager (joel16).
 * UI uses dual VRAM paint + SetFrameBuf (no SwapBuffers) to kill flicker.
 */

#include "common.h"
#include "gui.h"
#include "video.h"
#include "main.h"
#include "mh_print.h"
#include "input.h"
#include "wifi_receive.h"

#include <pspctrl.h>
#include <pspkernel.h>
#include <pspdisplay.h>
#include <psppower.h>
#include <psputility.h>
#include <pspnet.h>
#include <pspnet_inet.h>
#include <pspnet_apctl.h>
#include <pspwlan.h>

#include <sys/socket.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define FOG_PORT            2121
#define FOG_MAX_FILE_SIZE   (32 * 1024 * 1024)
#define FOG_CHUNK           (16 * 1024)

#define WR_COLOR_BG       COLOR15(8, 15, 12)
#define WR_COLOR_ACTIVE   COLOR15(31, 31, 29)
#define WR_COLOR_INACTIVE COLOR15(12, 25, 18)
#define WR_COLOR_HELP     COLOR15(20, 28, 22)

extern void *draw_frame;
extern void *disp_frame;
extern u32 option_clock_speed;

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

static char ALIGN_PSPDATA g_gu_list[0x10000];

static void wr_set_status(const char *s)
{
  strncpy(g_status, s ? s : "", sizeof(g_status) - 1);
  g_status[sizeof(g_status) - 1] = 0;
}

static void wr_set_detail(const char *s)
{
  strncpy(g_detail, s ? s : "", sizeof(g_detail) - 1);
  g_detail[sizeof(g_detail) - 1] = 0;
}

/* Paint into an absolute VRAM frame pointer (no flip). */
static void wr_paint_buf(void *frame)
{
  char line[96];
  void *saved = draw_frame;

  draw_frame = frame;
  clear_screen(COLOR15_TO_32(WR_COLOR_BG));
  print_string("Fog Wi-Fi Receive", 18, 16, WR_COLOR_HELP, BG_NO_FILL);
  print_string(g_status[0] ? g_status : "...", 18, 48, WR_COLOR_ACTIVE, BG_NO_FILL);
  if (g_detail[0])
    print_string(g_detail, 18, 72, WR_COLOR_INACTIVE, BG_NO_FILL);

  if (g_state == WR_RECEIVING)
  {
    sprintf(line, "Progress: %d%%", g_progress);
    print_string(line, 18, 110, WR_COLOR_ACTIVE, BG_NO_FILL);
  }

  print_string("O / X / START: Exit", 18, 250, WR_COLOR_HELP, BG_NO_FILL);
  draw_frame = saved;
}

/*
 * Stable UI: identical pixels in BOTH framebuffers, then SetFrameBuf.
 * Never calls sceGuSwapBuffers — that was fighting FogGBA software UI.
 */
static void wr_frame(void)
{
  wr_paint_buf((void *)0);
  wr_paint_buf((void *)(u32)PSP_FRAME_SIZE);
  sceDisplayWaitVblankStart();
  sceDisplaySetFrameBuf((void *)0x44000000, PSP_LINE_SIZE,
                        PSP_DISPLAY_PIXEL_FORMAT_5551,
                        PSP_DISPLAY_SETBUF_IMMEDIATE);
  disp_frame = (void *)0;
  draw_frame = (void *)(u32)PSP_FRAME_SIZE;
}

static void wait_pad_release(void)
{
  int guard = 0;
  while (get_pad_input(0x0001FFFF) != 0 && guard < 180)
  {
    sceDisplayWaitVblankStart();
    guard++;
  }
}

static int exit_pressed(void)
{
  u32 pad = get_pad_input(0x0001FFFF);
  return (pad & (PSP_CTRL_START | PSP_CTRL_CIRCLE | PSP_CTRL_CROSS)) != 0;
}

static void wait_for_exit_button(void)
{
  wait_pad_release();
  wr_set_detail("Press O / X / START to return");
  while (!exit_pressed())
    wr_frame();
  wait_pad_release();
}

/* Restore FogGBA double-buffer + texture GU state for the main menu. */
static void wr_restore_video(void)
{
  sceGuStart(GU_DIRECT, g_gu_list);
  sceGuDrawBuffer(GU_PSM_5551, (void *)(u32)PSP_FRAME_SIZE, PSP_LINE_SIZE);
  sceGuDispBuffer(PSP_SCREEN_WIDTH, PSP_SCREEN_HEIGHT, (void *)0, PSP_LINE_SIZE);
  sceGuOffset(2048 - (PSP_SCREEN_WIDTH / 2), 2048 - (PSP_SCREEN_HEIGHT / 2));
  sceGuViewport(2048, 2048, PSP_SCREEN_WIDTH, PSP_SCREEN_HEIGHT);
  sceGuScissor(0, 0, PSP_SCREEN_WIDTH, PSP_SCREEN_HEIGHT);
  sceGuEnable(GU_SCISSOR_TEST);
  sceGuDisable(GU_DEPTH_TEST);
  sceGuDisable(GU_BLEND);
  sceGuEnable(GU_TEXTURE_2D);
  sceGuTexMode(GU_PSM_5551, 0, 0, GU_FALSE);
  sceGuTexFunc(GU_TFX_REPLACE, GU_TCC_RGBA);
  sceGuFinish();
  sceGuSync(0, 0);

  disp_frame = (void *)0;
  draw_frame = (void *)(u32)PSP_FRAME_SIZE;

  wr_paint_buf((void *)0);
  wr_paint_buf((void *)(u32)PSP_FRAME_SIZE);
  sceDisplayWaitVblankStart();
  sceDisplaySetFrameBuf((void *)0x44000000, PSP_LINE_SIZE,
                        PSP_DISPLAY_PIXEL_FORMAT_5551,
                        PSP_DISPLAY_SETBUF_NEXTFRAME);
}

static int net_load_modules(void)
{
  int err;

  wr_set_status("Loading net modules...");
  wr_frame();

  err = sceUtilityLoadNetModule(PSP_NET_MODULE_COMMON);
  if (err < 0 && err != (int)0x80110800)
    return err;
  sceKernelDelayThread(150 * 1000);

  err = sceUtilityLoadNetModule(PSP_NET_MODULE_INET);
  if (err < 0 && err != (int)0x80110800)
    return err;
  sceKernelDelayThread(150 * 1000);
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

  wr_set_status("Init network...");
  wr_frame();

  /* Same pool sizes as CM File Manager */
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
  sceNetApctlDisconnect();
  sceKernelDelayThread(300 * 1000);
  sceNetApctlTerm();
  sceNetInetTerm();
  sceNetTerm();
  g_net_inited = 0;
}

static int read_ip(char *ip_out, int ip_out_len)
{
  union SceNetApctlInfo info;
  memset(&info, 0, sizeof(info));
  if (sceNetApctlGetInfo(PSP_NET_APCTL_INFO_IP, &info) < 0)
    return -1;
  if (info.ip[0] == 0 || strcmp(info.ip, "0.0.0.0") == 0)
    return -1;
  strncpy(ip_out, info.ip, ip_out_len - 1);
  ip_out[ip_out_len - 1] = 0;
  return 0;
}

static int apctl_has_ip(char *ip_out, int ip_out_len)
{
  int state = 0;
  if (sceNetApctlGetState(&state) < 0)
    return 0;
  if (state != PSP_NET_APCTL_STATE_GOT_IP)
    return 0;
  return read_ip(ip_out, ip_out_len) == 0;
}

/* CM File Manager style netconf loop (see joel16/CMFileManager-PSP net.cpp). */
static int net_show_connect_dialog(void)
{
  pspUtilityNetconfData data;
  struct pspUtilityNetconfAdhoc adhocparam;
  int done = 0;
  int err;
  int status;

  memset(&adhocparam, 0, sizeof(adhocparam));
  memset(&data, 0, sizeof(data));

  data.base.size = sizeof(data);
  sceUtilityGetSystemParamInt(PSP_SYSTEMPARAM_ID_INT_LANGUAGE, &data.base.language);
  sceUtilityGetSystemParamInt(PSP_SYSTEMPARAM_ID_INT_BUTTON_SWAP, &data.base.buttonSwap);
  data.base.graphicsThread = 17;
  data.base.accessThread = 19;
  data.base.fontThread = 18;
  data.base.soundThread = 16;
  data.action = PSP_NETCONF_ACTION_CONNECTAP;
  data.hotspot = 0;
  data.wifisp = 0;
  data.adhocparam = &adhocparam;

  wr_set_status("Opening Wi-Fi menu...");
  wr_set_detail("Same dialog as CM File Manager");
  wr_frame();

  err = sceUtilityNetconfInitStart(&data);
  if (err < 0)
  {
    sprintf(g_detail, "Netconf 0x%08X", (unsigned)err);
    return err;
  }

  while (!done)
  {
    /* g2dClear equivalent */
    sceGuStart(GU_DIRECT, g_gu_list);
    sceGuDisable(GU_TEXTURE_2D);
    sceGuDisable(GU_DEPTH_TEST);
    sceGuDisable(GU_BLEND);
    sceGuClearColor(0xFF322732); /* dark UI bg */
    sceGuClearDepth(0);
    sceGuClear(GU_COLOR_BUFFER_BIT | GU_DEPTH_BUFFER_BIT);
    sceGuFinish();
    sceGuSync(0, 0);

    status = sceUtilityNetconfGetStatus();
    switch (status)
    {
      case PSP_UTILITY_DIALOG_NONE:
        done = 1;
        break;

      case PSP_UTILITY_DIALOG_VISIBLE:
        err = sceUtilityNetconfUpdate(1);
        if (err < 0)
          done = 1;
        break;

      case PSP_UTILITY_DIALOG_QUIT:
        err = sceUtilityNetconfShutdownStart();
        if (err < 0)
          done = 1;
        break;

      case PSP_UTILITY_DIALOG_FINISHED:
        done = 1;
        break;

      default:
        break;
    }

    /* g2dFlip(G2D_VSYNC) */
    sceDisplayWaitVblankStart();
    disp_frame = draw_frame;
    draw_frame = sceGuSwapBuffers();
  }

  /* Back to flicker-free software UI */
  wr_set_status("Wi-Fi menu closed");
  wr_set_detail("");
  wr_frame();
  return 0;
}

static int net_ensure_ip(char *ip_out, int ip_out_len)
{
  int i;
  int state = 0;

  if (sceWlanGetSwitchState() == 0)
  {
    wr_set_detail("Flip WLAN switch ON");
    return -1;
  }

  if (apctl_has_ip(ip_out, ip_out_len))
    return 0;

  /* System Wi-Fi dialog (CM style). User may cancel — respect that. */
  net_show_connect_dialog();

  if (apctl_has_ip(ip_out, ip_out_len))
    return 0;

  /* Short settle for DHCP only if Apctl is still connecting */
  sceNetApctlGetState(&state);
  if (state == PSP_NET_APCTL_STATE_SCANNING ||
      state == PSP_NET_APCTL_STATE_JOINING ||
      state == PSP_NET_APCTL_STATE_GETTING_IP ||
      state == PSP_NET_APCTL_STATE_EAP_AUTH ||
      state == PSP_NET_APCTL_STATE_KEY_EXCHANGE)
  {
    wr_set_status("Finishing connection...");
    for (i = 0; i < 5 * 60; i++)
    {
      if (apctl_has_ip(ip_out, ip_out_len))
        return 0;
      if ((i % 30) == 0)
        wr_frame();
      else
        sceDisplayWaitVblankStart();
    }
  }

  /* Cancelled or failed — do NOT scan profiles (that forced a connect). */
  if (apctl_has_ip(ip_out, ip_out_len))
    return 0;

  sceNetApctlDisconnect();
  wr_set_status("Cancelled");
  wr_set_detail("No Wi-Fi connection");
  return -1;
}

/* Non-blocking accept via select (fcntl does not work on PSP inet sockets). */
static int sock_accept_nb(int listen_fd, struct sockaddr_in *addr, socklen_t *addrlen)
{
  fd_set rfds;
  struct SceNetInetTimeval tv;
  int r;

  FD_ZERO(&rfds);
  FD_SET(listen_fd, &rfds);
  tv.tv_sec = 0;
  tv.tv_usec = 0;

  r = sceNetInetSelect(listen_fd + 1, &rfds, NULL, NULL, &tv);
  if (r <= 0)
    return -1;

  return sceNetInetAccept(listen_fd, (struct sockaddr *)addr, addrlen);
}

static int sock_send_all(int fd, const void *buf, int len)
{
  const char *p = (const char *)buf;
  int sent = 0;

  while (sent < len)
  {
    int n = (int)sceNetInetSend(fd, p + sent, (size_t)(len - sent), 0);
    if (n < 0)
    {
      sceKernelDelayThread(1000);
      continue;
    }
    if (n == 0)
      return -1;
    sent += n;
  }
  return 0;
}

static int sock_recv_line(int fd, char *buf, int buflen, int timeout_frames)
{
  int pos = 0;
  int frames = 0;

  while (pos < buflen - 1)
  {
    char c;
    int n = (int)sceNetInetRecv(fd, &c, 1, 0);
    if (n < 0)
    {
      if (exit_pressed())
        return -2;
      sceDisplayWaitVblankStart();
      frames++;
      if (frames > timeout_frames)
        return -1;
      continue;
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
    frames = 0;
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

  if (sock_send_all(client, "FOGGBA 1\n", 9) < 0)
    goto done;

  if (sock_recv_line(client, line, sizeof(line), 30 * 60) < 0)
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

  if (sock_recv_line(client, line, sizeof(line), 30 * 60) < 0)
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
  wr_frame();

  while (received < size)
  {
    int want = (int)((size - received) > FOG_CHUNK ? FOG_CHUNK : (size - received));
    int n;

    if (exit_pressed())
    {
      sock_send_all(client, "ERR cancelled\n", 14);
      goto done;
    }

    n = (int)sceNetInetRecv(client, chunk, (size_t)want, 0);
    if (n < 0)
    {
      sceKernelDelayThread(1000);
      continue;
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
    if ((received & 0x7FFF) == 0 || received == size)
      wr_frame();
  }

  sock_send_all(client, "OK\n", 3);
  sprintf(g_status, "Saved: %s", filename);
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
  int yes = 1;

  g_state = WR_IDLE;
  g_progress = 0;
  ip[0] = 0;

  wait_pad_release();

  /* CM does this around FTP — helps DHCP on real hardware */
  scePowerLock(0);
  set_cpu_clock(PSP_CLOCK_222);

  wr_set_status("Starting...");
  wr_set_detail("");
  wr_frame();

  err = net_load_modules();
  if (err < 0)
  {
    wr_set_status("Net module load failed");
    sprintf(g_detail, "0x%08X", (unsigned)err);
    g_state = WR_ERROR;
    goto show_and_exit;
  }

  err = net_stack_init();
  if (err < 0)
  {
    wr_set_status("Network init failed");
    sprintf(g_detail, "0x%08X", (unsigned)err);
    g_state = WR_ERROR;
    goto show_and_exit;
  }

  if (net_ensure_ip(ip, sizeof(ip)) < 0)
  {
    wr_set_status("No Wi-Fi IP");
    if (!g_detail[0])
      wr_set_detail("Same AP that works in CM");
    g_state = WR_ERROR;
    goto show_and_exit;
  }

  wr_set_status("Opening port 2121...");
  wr_set_detail(ip);
  wr_frame();

  /* PSP needs sceNetInet* — libc socket() fails on real hardware. */
  listen_fd = sceNetInetSocket(AF_INET, SOCK_STREAM, 0);
  if (listen_fd < 0)
  {
    wr_set_status("socket() failed");
    sprintf(g_detail, "inet_errno=%d", sceNetInetGetErrno());
    g_state = WR_ERROR;
    goto show_and_exit;
  }

  sceNetInetSetsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));

  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons(FOG_PORT);
  addr.sin_addr.s_addr = INADDR_ANY;

  if (sceNetInetBind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0)
  {
    wr_set_status("bind() failed");
    sprintf(g_detail, "inet_errno=%d", sceNetInetGetErrno());
    g_state = WR_ERROR;
    goto show_and_exit;
  }

  if (sceNetInetListen(listen_fd, 1) < 0)
  {
    wr_set_status("listen() failed");
    sprintf(g_detail, "inet_errno=%d", sceNetInetGetErrno());
    g_state = WR_ERROR;
    goto show_and_exit;
  }

  sprintf(g_status, "IP: %s  Port: %d", ip, FOG_PORT);
  wr_set_detail("Waiting for FogConnect...");
  g_state = WR_WAITING;
  wait_pad_release();

  while (g_state == WR_WAITING)
  {
    if (exit_pressed())
      break;

    addrlen = sizeof(addr);
    client_fd = sock_accept_nb(listen_fd, &addr, &addrlen);
    if (client_fd >= 0)
    {
      wr_set_detail("PC connected...");
      wr_frame();
      handle_client(client_fd, dir_roms);
      sceNetInetClose(client_fd);
      client_fd = -1;

      if (g_state == WR_DONE)
        break;

      g_state = WR_WAITING;
      sprintf(g_status, "IP: %s  Port: %d", ip, FOG_PORT);
      wr_set_detail("Waiting for FogConnect...");
    }

    wr_frame();
  }

show_and_exit:
  if (g_state == WR_DONE)
    wr_set_detail("Transfer OK");

  wait_for_exit_button();

  if (client_fd >= 0)
    sceNetInetClose(client_fd);
  if (listen_fd >= 0)
    sceNetInetClose(listen_fd);

  net_stack_term();
  net_unload_modules();
  wr_restore_video();

  set_cpu_clock(option_clock_speed);
  scePowerUnlock(0);
}
