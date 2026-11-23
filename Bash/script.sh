#!/usr/bin/env bash
# =============================================================================
# ETL: Satisfacción de vida (OCDE, BLI 2015) + PIB per cápita (FMI, 2015)
#
# Produce:  merged_data.csv  (Pais,Satisfaccion_Vida,PIB_Capita)
#           X.csv            (PIB per cápita, sin encabezado)
#           y.csv            (Satisfacción de vida, sin encabezado)
#
# Uso:  ./script.sh [oecd.xlsx] [gdp.xlsx] [dir_salida]
# Requiere solo herramientas Unix: bash, unzip, awk, sort, join, cut, sed, tr
# =============================================================================
set -euo pipefail

# Orden por bytes: sort y join DEBEN usar la misma configuración regional
export LC_ALL=C

OECD_XLSX="${1:-/mnt/user-data/uploads/oecd_bli_2015.xlsx}"
GDP_XLSX="${2:-/mnt/user-data/uploads/gdp_per_capita.xlsx}"
OUT_DIR="${3:-/mnt/user-data/outputs}"

# Directorio temporal que se borra al terminar (o si hay error)
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

for cmd in unzip awk sort join cut sed tr uniq; do
  command -v "$cmd" >/dev/null || { echo "ERROR: falta la herramienta '$cmd'" >&2; exit 1; }
done

# -----------------------------------------------------------------------------
# PASO 0 · Conversión XLSX -> TSV (sin Python)
# Un .xlsx es un ZIP con XML: unzip extrae la hoja y awk reconstruye las filas.
# Las fechas se conservan como número serial de Excel; se decodifican en el paso 2.
# -----------------------------------------------------------------------------
xlsx_to_tsv() {
  local xlsx="$1" out="$2" sst="$WORK/sst.txt"

  # Tabla de cadenas compartidas: una por línea (índice = número de línea - 1)
  unzip -p "$xlsx" xl/sharedStrings.xml | tr -d '\r' \
    | sed 's#</si>#\n#g; s#<[^>]*>##g' > "$sst"

  # Hoja 1: una celda por línea; awk arma las filas separadas por tabuladores
  unzip -p "$xlsx" xl/worksheets/sheet1.xml | tr -d '\r' \
    | sed 's#<c #\n<c #g' \
    | awk -v SST="$sst" '
      function colnum(s,  i, n) {
        n = 0
        for (i = 1; i <= length(s); i++)
          n = n * 26 + index("ABCDEFGHIJKLMNOPQRSTUVWXYZ", substr(s, i, 1))
        return n
      }
      BEGIN {
        k = 0
        while ((getline line < SST) > 0) {
          gsub(/&lt;/, "<", line); gsub(/&gt;/, ">", line); gsub(/&quot;/, "\"", line)
          gsub(/&apos;/, "\047", line); gsub(/&amp;/, "\\&", line); gsub(/\t/, " ", line)
          SS[k++] = line
        }
        close(SST)
      }
      /<c r="/ {
        match($0, /r="[A-Z]+[0-9]+"/)
        ref = substr($0, RSTART + 3, RLENGTH - 4)
        letters = ref; sub(/[0-9]+$/, "", letters)
        rown = ref;    sub(/^[A-Z]+/, "", rown)
        r = rown + 0; c = colnum(letters)
        val = ""
        if (match($0, /<v>[^<]*<\/v>/)) val = substr($0, RSTART + 3, RLENGTH - 7)
        if ($0 ~ / t="s"/) val = SS[val + 0]
        CELL[r, c] = val
        if (r > maxr) maxr = r
        if (c > maxc) maxc = c
      }
      END {
        for (r = 1; r <= maxr; r++) {
          line = ""
          for (c = 1; c <= maxc; c++)
            line = line (c > 1 ? "\t" : "") ((r, c) in CELL ? CELL[r, c] : "")
          print line
        }
      }' > "$out"
}

echo "== PASO 0: conversión XLSX -> TSV =="
xlsx_to_tsv "$OECD_XLSX" "$WORK/oecd.tsv"
xlsx_to_tsv "$GDP_XLSX"  "$WORK/gdp.tsv"

# -----------------------------------------------------------------------------
# PASO 1 · Análisis exploratorio: líneas, delimitadores y encabezados
# -----------------------------------------------------------------------------
echo "== PASO 1: análisis exploratorio =="
for f in oecd gdp; do
  echo "--- $f ---"
  echo "Líneas (wc -l): $(wc -l < "$WORK/$f.tsv")"
  echo "Campos por línea (delimitador tabulador, awk):"
  awk -F'\t' '{ print NF }' "$WORK/$f.tsv" | sort | uniq -c
  echo "Primeras líneas (head + cat -A, ^I = tabulador):"
  head -n 2 "$WORK/$f.tsv" | cat -A | cut -c1-200
done

# -----------------------------------------------------------------------------
# PASO 2 · Filtrar satisfacción de vida general (SW_LIFS) y desigualdad total (TOT)
# Columnas OCDE: 2=Country, 3=INDICATOR, 7=INEQUALITY, 15=Value
# Excel convirtió algunos valores como 7.3 en fechas (día=7, mes=3); se revierten.
# -----------------------------------------------------------------------------
echo "== PASO 2: filtrar satisfacción de vida (SW_LIFS, TOT) =="
awk -F'\t' -v OFS=',' '
  function fail(msg) { print "ERROR: " msg | "cat 1>&2"; exit 1 }

  # Serial de Excel -> "día.mes". Serial 46023 = 2026-01-01; todas las fechas son de 2026.
  function decode(v,  doy, m, d, md) {
    if (v < 100) return v
    if (v < 46023 || v > 46387) fail("serial de fecha fuera de 2026: " v)
    split("31 28 31 30 31 30 31 31 30 31 30 31", md, " ")
    doy = v - 46022
    for (m = 1; doy > md[m]; m++) doy -= md[m]
    d = doy
    return d + m / 10
  }

  NR > 1 && $3 == "SW_LIFS" && $7 == "TOT" && $2 != "OECD - Total" {
    if ($15 == "") fail("valor vacío en " $2)
    if ($2 ~ /,/)  fail("nombre con coma: " $2)
    val = decode($15 + 0)
    if (val < 0 || val > 10) fail("valor fuera de rango en " $2 ": " val)
    printf "%s,%.1f\n", $2, val
  }
' "$WORK/oecd.tsv" > "$WORK/oecd_sat.csv"
echo "Países con satisfacción de vida: $(wc -l < "$WORK/oecd_sat.csv")"

# -----------------------------------------------------------------------------
# PASO 3 · Normalizar PIB 2015 (FMI): quitar comas de miles, elegir columnas,
# convertir a CSV. Descarta pies de página, "n/a" y vacíos.
# -----------------------------------------------------------------------------
echo "== PASO 3: normalizar PIB 2015 (FMI) =="
awk -F'\t' -v OFS=',' '
  NR == 1 {
    for (i = 1; i <= NF; i++) if ($i ~ /^2015(\.0)?$/) col = i
    if (!col) { print "ERROR: no hay columna 2015" | "cat 1>&2"; exit 1 }
    next
  }
  {
    name = $1; v = $col
    gsub(/^ +| +$/, "", name)
    gsub(/,/, "", v)                      # 50,961.865 -> 50961.865
    if (name == "" || name ~ /,/) next
    if (v !~ /^[0-9]+(\.[0-9]+)?$/) next  # descarta "n/a", vacíos, notas
    print name, v
  }' "$WORK/gdp.tsv" > "$WORK/gdp_pib.csv"
echo "Países con PIB 2015 válido: $(wc -l < "$WORK/gdp_pib.csv")"

# -----------------------------------------------------------------------------
# PASO 4 · Ordenar y fusionar (inner join por país)
# -----------------------------------------------------------------------------
echo "== PASO 4: ordenar y fusionar =="
sort -t, -k1,1 "$WORK/oecd_sat.csv" -o "$WORK/oecd_sorted.csv"
sort -t, -k1,1 "$WORK/gdp_pib.csv"  -o "$WORK/gdp_sorted.csv"

# join requiere claves únicas
for f in oecd_sorted gdp_sorted; do
  dups=$(cut -d, -f1 "$WORK/$f.csv" | uniq -d | wc -l)
  [ "$dups" -eq 0 ] || { echo "ERROR: países duplicados en $f" >&2; exit 1; }
done

# Encabezado + inner join; -o toma país y satisfacción (archivo 1) y PIB (archivo 2)
{
  echo "Pais,Satisfaccion_Vida,PIB_Capita"
  join -t, -1 1 -2 1 -o 1.1,1.2,2.2 "$WORK/oecd_sorted.csv" "$WORK/gdp_sorted.csv"
} > "$WORK/merged_data.csv"

# Reporte de países sin pareja en el FMI
sin_pareja=$(join -v 1 -t, -1 1 -2 1 "$WORK/oecd_sorted.csv" "$WORK/gdp_sorted.csv" | cut -d, -f1)
[ -z "$sin_pareja" ] || echo "AVISO: sin pareja en FMI: $(echo "$sin_pareja" | tr '\n' ';')"
echo "Filas fusionadas (sin encabezado): $(( $(wc -l < "$WORK/merged_data.csv") - 1 ))"

# -----------------------------------------------------------------------------
# PASO 5 · Separar X (PIB) e y (satisfacción), sin encabezado
# -----------------------------------------------------------------------------
echo "== PASO 5: generar X.csv e y.csv =="
tail -n +2 "$WORK/merged_data.csv" | cut -d, -f3 > "$WORK/X.csv"
tail -n +2 "$WORK/merged_data.csv" | cut -d, -f2 > "$WORK/y.csv"

# -----------------------------------------------------------------------------
# PASO 6 · Entregar resultados
# -----------------------------------------------------------------------------
echo "== PASO 6: copiar a $OUT_DIR =="
mkdir -p "$OUT_DIR"
cp "$WORK/merged_data.csv" "$WORK/X.csv" "$WORK/y.csv" "$OUT_DIR/"
echo "Listo."
