# Read-only Windows processor affinity and drive topology for the WSL bridge.
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class Btop4wslCore {
    public int[] Logical;
    public int Efficiency;
}
public static class Btop4wslTopology {
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool GetLogicalProcessorInformationEx(int relation, IntPtr buffer, ref uint length);
    public static Btop4wslCore[] Read() {
        uint length = 0;
        GetLogicalProcessorInformationEx(0, IntPtr.Zero, ref length);
        if (length == 0) throw new InvalidOperationException("No processor topology available");
        IntPtr buffer = Marshal.AllocHGlobal((int)length);
        try {
            if (!GetLogicalProcessorInformationEx(0, buffer, ref length))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            var cores = new List<Btop4wslCore>();
            for (int offset=0; offset < length;) {
                IntPtr row = IntPtr.Add(buffer, offset);
                int size = Marshal.ReadInt32(row, 4);
                if (size < 32 || offset + size > length) throw new InvalidOperationException("Invalid processor record");
                int groups = (ushort)Marshal.ReadInt16(row, 30);
                int stride = IntPtr.Size + 8;
                var logical = new List<int>();
                for (int g=0; g<groups; ++g) {
                    int maskOffset = 32 + g*stride;
                    if (maskOffset + stride > size) throw new InvalidOperationException("Invalid processor affinity");
                    ulong mask = IntPtr.Size == 8 ? (ulong)Marshal.ReadInt64(row, maskOffset) : (uint)Marshal.ReadInt32(row, maskOffset);
                    int group = (ushort)Marshal.ReadInt16(row, maskOffset + IntPtr.Size);
                    for (int bit=0; bit<IntPtr.Size*8; ++bit)
                        if ((mask & (1UL << bit)) != 0) logical.Add(group*64 + bit);
                }
                cores.Add(new Btop4wslCore {Logical=logical.ToArray(), Efficiency=Marshal.ReadByte(row,9)});
                offset += size;
            }
            return cores.ToArray();
        } finally { Marshal.FreeHGlobal(buffer); }
    }
}
'@
$drives = @(Get-Partition -ErrorAction SilentlyContinue | Where-Object {$_.DriveLetter} |
    Select-Object DriveLetter,DiskNumber)
@{drives=$drives; cores=@([Btop4wslTopology]::Read())} | ConvertTo-Json -Depth 6 -Compress
