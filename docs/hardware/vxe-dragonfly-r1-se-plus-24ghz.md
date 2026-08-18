# HID inventory: VXE Dragonfly R1 SE+ (2.4GHz receiver)

VID:PID `3554:F58E` | host `windows` | 8 collection(s)

Read-only capture: no writes, no feature reports sent, no input reports read.

| if | usage page | usage | vendor | opened | desc bytes | fnv1a64 | reports |
|---|---|---|---|---|---|---|---|
| 0 | `0x0001` | `0x0006` | no | yes | 66 | `d681df95aa1eda7e` | 1 |
| 1 | `0x0001` | `0x0080` | no | yes | 29 | `80e01dbf86ba3578` | 1 |
| 1 | `0x000C` | `0x0001` | no | yes | 25 | `365d4c9a827c4d82` | 1 |
| 1 | `0xFF02` | `0x0002` | yes | yes | 36 | `c2bb04bd91f1d9b6` | 1 |
| 1 | `0xFF03` | `0x0000` | yes | yes | 23 | `63b37376354d464e` | 1 |
| 1 | `0xFF04` | `0x0002` | yes | yes | 23 | `deacbb83de706f1d` | 1 |
| 1 | `0xFF05` | `0x0000` | yes | yes | 23 | `b760328a99c26a1a` | 1 |
| 2 | `0x0001` | `0x0002` | no | yes | 89 | `25077c26ff7765e3` | 1 |

## interface 0 - usage `0x0001:0x0006`

- manufacturer: Compx
- product: VXE Mouse 1K Dongle
- serial: present, withheld from the report
- release: `0x0110`

```text
report   0: in    8  out    1  feature    -   (bytes, no report id)
```

<details><summary>report descriptor (66 bytes)</summary>

```text
05010906a101050719e029e715002501
750195088102750895018103190029ff
150026ff007508950681000508190129
03150025017501950391027505950191
03c0
```

</details>

## interface 1 - usage `0x0001:0x0080`

- manufacturer: Compx
- product: VXE Mouse 1K Dongle
- serial: present, withheld from the report
- release: `0x0110`

```text
report   3: in    2  out    -  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (29 bytes)</summary>

```text
05010980a10185031981298315002501
750195038102750595018103c0
```

</details>

## interface 1 - usage `0x000C:0x0001`

- manufacturer: Compx
- product: VXE Mouse 1K Dongle
- serial: present, withheld from the report
- release: `0x0110`

```text
report   5: in    3  out    -  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (25 bytes)</summary>

```text
050c0901a101850519002a3c02150026
3c02751095018100c0
```

</details>

## interface 1 - usage `0xFF02:0x0002` (vendor-defined)

- manufacturer: Compx
- product: VXE Mouse 1K Dongle
- serial: present, withheld from the report
- release: `0x0110`

```text
report   8: in   17  out   17  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (36 bytes)</summary>

```text
0602ff0902a10185080902150026ff00
7508951081000902150026ff00750895
109100c0
```

</details>

## interface 1 - usage `0xFF03:0x0000` (vendor-defined)

- manufacturer: Compx
- product: VXE Mouse 1K Dongle
- serial: present, withheld from the report
- release: `0x0110`

```text
report   2: in    8  out    -  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (23 bytes)</summary>

```text
0603ff0900a10185020900150026ff00
750895078102c0
```

</details>

## interface 1 - usage `0xFF04:0x0002` (vendor-defined)

- manufacturer: Compx
- product: VXE Mouse 1K Dongle
- serial: present, withheld from the report
- release: `0x0110`

```text
report   6: in    -  out    -  feature    8   (bytes, incl. report id)
```

<details><summary>report descriptor (23 bytes)</summary>

```text
0604ff0902a10185060902150026ff00
75089507b102c0
```

</details>

## interface 1 - usage `0xFF05:0x0000` (vendor-defined)

- manufacturer: Compx
- product: VXE Mouse 1K Dongle
- serial: present, withheld from the report
- release: `0x0110`

```text
report  16: in    8  out    -  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (23 bytes)</summary>

```text
0605ff0900a10185100900150026ff00
750895078102c0
```

</details>

## interface 2 - usage `0x0001:0x0002`

- manufacturer: Compx
- product: VXE Mouse 1K Dongle
- serial: present, withheld from the report
- release: `0x0110`

```text
report   0: in    7  out    -  feature    -   (bytes, no report id)
```

<details><summary>report descriptor (89 bytes)</summary>

```text
05010902a1010901a100050919012905
15002501750195058102750395018103
05010930093116008026ff7f75109502
8106c00900a10009381581257f750895
018106c00900a100050c0a3802158125
7f750895018106c0c0
```

</details>
