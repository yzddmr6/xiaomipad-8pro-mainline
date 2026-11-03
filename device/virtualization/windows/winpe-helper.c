/* SPDX-License-Identifier: MIT
 * Small ARM64 WinPE installer helper; builds on Linux, no CRT or Windows SDK.
 * Public Win32 ABI declarations from Microsoft Learn. Three fixed operations:
 * trust the packaged public test certificate, check RDMA interface presence,
 * create and bind the packaged ROOT\PVMPOWER device. Never touches host devices.
 */
typedef unsigned int DWORD;
typedef unsigned short WCHAR;
typedef unsigned char BYTE;
typedef int BOOL;
typedef void *HANDLE;
typedef const WCHAR *PCWSTR;
typedef struct { DWORD a; unsigned short b,c; BYTE d[8]; } GUID;
typedef struct { DWORD cbSize; GUID ClassGuid; DWORD DevInst; void *Reserved; } DEVINFO;
typedef struct { DWORD cbSize; GUID InterfaceClassGuid; DWORD Flags; void *Reserved; } IFACE;
#define DLL __declspec(dllimport)
#define INVALID ((HANDLE)(long long)-1)
DLL void ExitProcess(DWORD);
DLL DWORD GetLastError(void);
DLL HANDLE GetStdHandle(DWORD);
DLL BOOL WriteFile(HANDLE,const void *,DWORD,DWORD *,void *);
DLL HANDLE CreateFileW(PCWSTR,DWORD,DWORD,void *,DWORD,DWORD,HANDLE);
DLL BOOL ReadFile(HANDLE,void *,DWORD,DWORD *,void *);
DLL BOOL CloseHandle(HANDLE);
DLL PCWSTR GetCommandLineW(void);
DLL HANDLE LoadLibraryW(PCWSTR);
DLL void *GetProcAddress(HANDLE,const char *);
DLL HANDLE CertOpenStore(const char *,DWORD,HANDLE,DWORD,const void *);
DLL BOOL CertAddEncodedCertificateToStore(HANDLE,DWORD,const BYTE *,DWORD,DWORD,void *);
DLL BOOL CertCloseStore(HANDLE,DWORD);
DLL HANDLE SetupDiCreateDeviceInfoList(const GUID *,HANDLE);
DLL BOOL SetupDiCreateDeviceInfoW(HANDLE,PCWSTR,const GUID *,PCWSTR,HANDLE,DWORD,DEVINFO *);
DLL BOOL SetupDiSetDeviceRegistryPropertyW(HANDLE,DEVINFO *,DWORD,const BYTE *,DWORD);
DLL BOOL SetupDiCallClassInstaller(DWORD,HANDLE,DEVINFO *);
DLL BOOL SetupDiDestroyDeviceInfoList(HANDLE);
DLL HANDLE SetupDiGetClassDevsW(const GUID *,PCWSTR,HANDLE,DWORD);
DLL BOOL SetupDiEnumDeviceInterfaces(HANDLE,void *,const GUID *,DWORD,IFACE *);

static GUID system_class={0x4d36e97d,0xe325,0x11ce,{0xbf,0xc1,0x08,0x00,0x2b,0xe1,0x03,0x18}};
/* Public rdmapool interface ID, fixed upstream driver source. */
static GUID rdma_interface={0x7b5e2f3a,0x9c1d,0x4e8f,{0xa6,0xb2,0x3d,0x4c,0x5e,0x6f,0x7a,0x8b}};
static BYTE cert[8192];
static BOOL installed;
static void print(const char *s) { DWORD n=0,w;while(s[n])n++;WriteFile(GetStdHandle((DWORD)-11),s,n,&w,0); }
static void fail(const char *stage) {
    DWORD e=GetLastError(); char b[16]; int n=0;
    print(stage);print(" Win32 error=");do {b[n++]=(char)('0'+e%10);e/=10;} while(e);
    while(n){char c[2]={b[--n],0};print(c);}print("\r\n");ExitProcess(1);
}
static BOOL has(PCWSTR s, PCWSTR needle) {
    for(;*s;s++){DWORD n=0;while(needle[n]&&s[n]==needle[n])n++;if(!needle[n])return 1;}return 0;
}
static void trust(void) {
    HANDLE f=CreateFileW(installed ? L"C:\\PianoVM\\DroidVM_Test.cer" : L"X:\\piano\\DroidVM_Test.cer",0x80000000,1,0,3,0,0);DWORD bytes=0;
    if(f==INVALID)fail("certificate open");
    if(!ReadFile(f,cert,sizeof(cert),&bytes,0)||!bytes||bytes==sizeof(cert))fail("certificate read");CloseHandle(f);
    PCWSTR names[]={L"ROOT",L"TrustedPublisher"};
    for(int i=0;i<2;i++){
        HANDLE store=CertOpenStore((const char *)10,0,0,0x20000,names[i]);
        if(!store)fail("certificate store");
        if(!CertAddEncodedCertificateToStore(store,1,cert,bytes,4,0))fail("certificate import");
        CertCloseStore(store,0);
    }
    print("PIANO_CERT_TRUSTED_LOCAL_MACHINE\r\n");
}
static void rdma(void) {
    HANDLE set=SetupDiGetClassDevsW(&rdma_interface,0,0,0x12);
    IFACE iface={0};iface.cbSize=sizeof(iface);
    if(set==INVALID||!SetupDiEnumDeviceInterfaces(set,0,&rdma_interface,0,&iface))fail("RDMA interface absent");
    SetupDiDestroyDeviceInfoList(set);print("PIANO_RDMA_INTERFACE_PRESENT\r\n");
}
static void power(void) {
    typedef BOOL (*UPDATE)(HANDLE,PCWSTR,PCWSTR,DWORD,BOOL *);
    HANDLE dll=LoadLibraryW(L"newdev.dll");if(!dll)fail("newdev.dll");
    UPDATE update=(UPDATE)GetProcAddress(dll,"UpdateDriverForPlugAndPlayDevicesW");if(!update)fail("newdev API");
    HANDLE set=SetupDiCreateDeviceInfoList(&system_class,0);if(set==INVALID)fail("power device set");
    DEVINFO dev={0};dev.cbSize=sizeof(dev);
    if(!SetupDiCreateDeviceInfoW(set,L"PVMPOWER",&system_class,L"Gunyah pVM Power Manager",0,1,&dev))fail("power device create");
    const WCHAR ids[]=L"ROOT\\PVMPOWER\0";
    if(!SetupDiSetDeviceRegistryPropertyW(set,&dev,1,(const BYTE *)ids,sizeof(ids)))fail("power hardware id");
    if(!SetupDiCallClassInstaller(0x19,set,&dev))fail("power register");
    BOOL reboot=0;
    if(!update(0,L"ROOT\\PVMPOWER",installed ? L"C:\\PianoVM\\drivers\\pvmpower\\pvmpower.inf" : L"X:\\piano\\drivers\\pvmpower\\pvmpower.inf",4,&reboot))fail("power driver install");
    SetupDiDestroyDeviceInfoList(set);
    if(reboot){print("PIANO_POWER_REQUIRES_REBOOT\r\n");ExitProcess(2);}
    print("PIANO_POWER_DEVNODE_BOUND\r\n");
}
void mainCRTStartup(void) {
    PCWSTR cmd=GetCommandLineW();
    installed=has(cmd,L"--installed");
    if(has(cmd,L"--trust"))trust();
    else if(has(cmd,L"--rdma"))rdma();
    else if(has(cmd,L"--power"))power();
    else {print("use --trust, --rdma, or --power\r\n");ExitProcess(64);}
    ExitProcess(0);
}
