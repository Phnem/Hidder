# HID inventory: AULA Hero 84 HE

VID:PID `372E:103E` | host `windows` | 7 collection(s)

Read-only capture: no writes, no feature reports sent, no input reports read.

| if | usage page | usage | vendor | opened | desc bytes | fnv1a64 | reports |
|---|---|---|---|---|---|---|---|
| 0 | `0x0001` | `0x0006` | no | yes | 66 | `109a9237d49eacf8` | 1 |
| 1 | `0x0001` | `0x0002` | no | yes | 76 | `cbf687674d395883` | 1 |
| 1 | `0x0001` | `0x0006` | no | yes | 65 | `e14b38659ede0f9e` | 1 |
| 1 | `0x0001` | `0x0080` | no | yes | 29 | `a05419723d11acc6` | 1 |
| 1 | `0x000C` | `0x0001` | no | yes | 25 | `6a7267dc6466e44b` | 1 |
| 2 | `0xFF00` | `0x0001` | yes | yes | 25 | `c0e89e9e96f42145` | 1 |
| 2 | `0xFF60` | `0x0061` | yes | yes | 36 | `57273a389450efe7` | 1 |

## interface 0 - usage `0x0001:0x0006`

- manufacturer: BY Tech
- product: HERO 84 HE
- serial: present, withheld from the report
- release: `0x0216`

```text
report   0: in    8  out    1  feature    -   (bytes, no report id)
```

<details><summary>report descriptor (66 bytes)</summary>

```text
05010906a101050719e029e715002501
750195088102750895018103190029ff
150026ff007508950681000508190129
05150025017501950591027503950191
03c0
```

</details>

## interface 1 - usage `0x0001:0x0002`

- manufacturer: BY Tech
- product: HERO 84 HE
- serial: present, withheld from the report
- release: `0x0216`

```text
report   2: in    5  out    -  feature    2   (bytes, incl. report id)
```

<details><summary>report descriptor (76 bytes)</summary>

```text
05010902a1010901a100850205091901
29051500250175019505810275039501
810305010930093109381581257f7508
95038106c005ff093c09011500250175
019502b12275069501b103c0
```

</details>

## interface 1 - usage `0x0001:0x0006`

- manufacturer: BY Tech
- product: HERO 84 HE
- serial: present, withheld from the report
- release: `0x0216`

```text
report   7: in   16  out    -  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (65 bytes)</summary>

```text
05010906a1018507050719e029e71500
25017501950881021900296715002501
7501956881020985098709880989098a
098b0991099215002501750195088102
c0
```

</details>

## interface 1 - usage `0x0001:0x0080`

- manufacturer: BY Tech
- product: HERO 84 HE
- serial: present, withheld from the report
- release: `0x0216`

```text
report   5: in    2  out    -  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (29 bytes)</summary>

```text
05010980a10185051981298315002501
750195038102750595018103c0
```

</details>

## interface 1 - usage `0x000C:0x0001`

- manufacturer: BY Tech
- product: HERO 84 HE
- serial: present, withheld from the report
- release: `0x0216`

```text
report   4: in    3  out    -  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (25 bytes)</summary>

```text
050c0901a101850419002aff1f150026
ff1f751095018100c0
```

</details>

## interface 2 - usage `0xFF00:0x0001` (vendor-defined)

- manufacturer: BY Tech
- product: HERO 84 HE
- serial: present, withheld from the report
- release: `0x0216`

```text
report   3: in    -  out    -  feature   64   (bytes, incl. report id)
```

<details><summary>report descriptor (25 bytes)</summary>

```text
0600ff0901a101850319012902150026
ff007508953fb102c0
```

</details>

## interface 2 - usage `0xFF60:0x0061` (vendor-defined)

- manufacturer: BY Tech
- product: HERO 84 HE
- serial: present, withheld from the report
- release: `0x0216`

```text
report   9: in   64  out   64  feature    -   (bytes, incl. report id)
```

<details><summary>report descriptor (36 bytes)</summary>

```text
0660ff0961a10185090962150026ff00
7508953f81020963150026ff00750895
3f9102c0
```

</details>
