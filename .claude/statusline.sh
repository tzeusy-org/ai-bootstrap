#!/usr/bin/env bash
# Claude Code StatusLine - cross-platform (macOS + Ubuntu), stdin JSON only (no API calls)
# Line 1: model | effort | thinking | session name | branch
# Line 2: ctx bar | 5h bar + reset | 7d bar + reset | cache health

INPUT=$(cat)
if [ -z "$INPUT" ]; then printf "Claude"; exit 0; fi
if ! command -v jq &>/dev/null; then printf "Claude (install jq)"; exit 0; fi

blue=$'\033[38;2;0;153;255m'
orange=$'\033[38;2;255;176;85m'
green=$'\033[38;2;0;160;0m'
cyan=$'\033[38;2;46;149;153m'
red=$'\033[38;2;255;85;85m'
yellow=$'\033[38;2;230;200;0m'
white=$'\033[38;2;220;220;220m'
dim=$'\033[2m'
reset=$'\033[0m'
sep=" ${dim}|${reset} "

format_tokens() {
    local n=$1
    if [ "$n" -ge 1000000 ]; then awk "BEGIN { printf \"%.1fm\", $n/1000000 }"
    elif [ "$n" -ge 1000 ]; then awk "BEGIN { printf \"%dk\", int($n/1000 + 0.5) }"
    else printf "%d" "$n"; fi
}

# Percent -> color (green/orange/yellow/red)
pct_color() {
    local pct=$1
    if [ "$pct" -ge 90 ]; then printf "%s" "$red"
    elif [ "$pct" -ge 70 ]; then printf "%s" "$yellow"
    elif [ "$pct" -ge 50 ]; then printf "%s" "$orange"
    else printf "%s" "$green"; fi
}

build_bar() {
    local pct=$1 width=$2
    [ "$pct" -lt 0 ] && pct=0
    [ "$pct" -gt 100 ] && pct=100
    local filled=$(( pct * width / 100 )) i filled_str="" empty_str=""
    for ((i=0; i<filled; i++)); do filled_str+="●"; done
    for ((i=filled; i<width; i++)); do empty_str+="○"; done
    printf "%s%s%s%s%s" "$(pct_color "$pct")" "$filled_str" "$dim" "$empty_str" "$reset"
}

# epoch -> local time ("time" = 6:20am, "date" = Oct 3)
format_epoch() {
    local epoch=$1 style=$2 fmt
    [ -z "$epoch" ] && return
    if [ "$style" = "time" ]; then fmt="+%-I:%M%p"; else fmt="+%b %-d"; fi
    if [[ "$OSTYPE" == darwin* ]]; then date -r "$epoch" "$fmt"; else date -d "@$epoch" "$fmt"; fi 2>/dev/null | sed 's/AM$/am/;s/PM$/pm/'
}

# Single jq pass; \x1f-separated so empty fields survive `read`.
IFS=$'\x1f' read -r model effort thinking session cwd size ctx_pct used \
    five_pct five_reset seven_pct seven_reset \
    cache_warm cache_exp cache_hit cache_miss over200k < <(
    echo "$INPUT" | jq -r '[
        (.model.display_name // "Claude"),
        (.effort.level // "-"),
        (.thinking.enabled // false),
        (.session_name // "-"),
        (.workspace.current_dir // .cwd // "-"),
        (.context_window.context_window_size // 200000),
        (.context_window.used_percentage // 0 | floor),
        ((.context_window.current_usage // {}) | (.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)),
        (.rate_limits.five_hour.used_percentage // "-" | if type=="number" then (.+0.5|floor) else . end),
        (.rate_limits.five_hour.resets_at // "-"),
        (.rate_limits.seven_day.used_percentage // "-" | if type=="number" then (.+0.5|floor) else . end),
        (.rate_limits.seven_day.resets_at // "-"),
        (.prompt_cache.warm // "-"),
        (.prompt_cache.expires_at // "-"),
        (.prompt_cache.hit_ratio // "-" | if type=="number" then (.*100|floor) else . end),
        (.prompt_cache.misses // 0),
        (.exceeds_200k_tokens // false)
    ] | map(tostring) | join("\u001f")'
)

# Persist rate limits for later reading (atomic write; skipped when absent).
if [ "$five_pct" != "-" ] || [ "$seven_pct" != "-" ]; then
    rl_file="${CLAUDE_RATE_LIMITS_FILE:-$HOME/.claude/rate_limits.json}"
    echo "$INPUT" | jq --argjson now "$(date +%s)" '{
        updated_at: $now,
        five_hour: (.rate_limits.five_hour // null),
        seven_day: (.rate_limits.seven_day // null)
    }' > "$rl_file.$$" 2>/dev/null && mv -f "$rl_file.$$" "$rl_file" || rm -f "$rl_file.$$"
fi

# Effective window: CLAUDE_CODE_AUTO_COMPACT_WINDOW caps it (stdin pct uses the full model window).
win=${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-}
if [[ "$win" =~ ^[0-9]+$ ]] && [ "$win" -gt 0 ] && [ "$win" -lt "$size" ]; then
    size=$win
    ctx_pct=$(( used * 100 / size ))
fi

# ========== LINE 1: model | effort | thinking | session | branch ==========
line1="${blue}${model}${reset}"
[ "$effort" != "-" ] && line1+="${sep}effort ${yellow}${effort}${reset}"
if [ "$thinking" = "true" ]; then line1+="${sep}think ${orange}on${reset}"; else line1+="${sep}think ${dim}off${reset}"; fi
[ "$session" != "-" ] && line1+="${sep}${white}${session}${reset}"
if [ "$cwd" != "-" ]; then
    branch=$(git -C "$cwd" branch --show-current 2>/dev/null)
    repo=$(basename "$cwd")
    line1+="${sep}${cyan}${repo}${branch:+ ${dim}@${reset}${cyan} ${branch}}${reset}"
fi

# ========== LINE 2: ctx | 5h | 7d | cache ==========
line2="${white}ctx${reset} $(build_bar "$ctx_pct" 10) $(pct_color "$ctx_pct")${ctx_pct}%${reset} ${dim}$(format_tokens "$used")/$(format_tokens "$size")${reset}"
[ "$over200k" = "true" ] && line2+=" ${red}>200k${reset}"

if [ "$five_pct" != "-" ]; then
    line2+="${sep}${white}5h${reset} $(build_bar "$five_pct" 10) $(pct_color "$five_pct")${five_pct}%${reset}"
    [ "$five_reset" != "-" ] && line2+=" ${dim}↻$(format_epoch "$five_reset" time)${reset}"
fi
if [ "$seven_pct" != "-" ]; then
    line2+="${sep}${white}7d${reset} $(build_bar "$seven_pct" 10) $(pct_color "$seven_pct")${seven_pct}%${reset}"
    [ "$seven_reset" != "-" ] && line2+=" ${dim}↻$(format_epoch "$seven_reset" date)${reset}"
fi

# --- Cache health: warm + expiry clock time + hit ratio; red on misses ---
if [ "$cache_warm" != "-" ]; then
    # Statusline only re-renders on events, so show the absolute expiry, not a countdown that goes stale.
    left=""
    if [ "$cache_exp" != "-" ]; then
        if [ "$cache_exp" -gt "$(date +%s)" ]; then left=" until $(format_epoch "$cache_exp" time)"; else cache_warm=false; fi
    fi
    if [ "$cache_warm" = "true" ]; then cache="${green}cache warm${reset}${left}"; else cache="${red}cache cold${reset}"; fi
    [ "$cache_hit" != "-" ] && cache+=" ${dim}${cache_hit}% hit${reset}"
    [ "$cache_miss" -gt 0 ] 2>/dev/null && cache+=" ${red}${cache_miss} miss${reset}"
    line2+="${sep}${cache}"
fi

printf "%s\n%s" "$line1" "$line2"
