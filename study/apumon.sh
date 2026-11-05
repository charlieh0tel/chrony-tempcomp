#!/bin/bash

declare -A hwmon_map

for name in /sys/class/hwmon/hwmon*/name; do
  dir=$(dirname "$name")
  name=$(<"$name")
  hwmon_map["$name"]="$dir"
done

get_hwmon() {
    local sensor="$1"
    local what="$2"
    local scale=$3
    local dir="${hwmon_map["$sensor"]}"
    local value_file="${dir}/${what}"
    local value
    value=$(< "${value_file}")
    echo "scale=3; $value / $scale" | bc
}

print_header() {
    echo "t_unix,acpi_tz_C,k10_temp_C,fam15h_power_W"
}

print_one() {
    local t acpitz k10temp
    t=$(date +%s)
    acpitz=$(get_hwmon "acpitz" temp1_input 1000)
    k10temp=$(get_hwmon "k10temp" temp1_input 1000)
    #fam15h_power=$(get_hwmon "fam15h_power" power1_average 1000000)

    echo "${t},${acpitz},${k10temp}" #,${fam15h_power}"
}

#print_header
while :; do
    print_one
    sleep 1
done
