#!/usr/bin/env bash
# Exercise the user-run harness in a throwaway base dir. Covers the direct
# branch only (invoker == RUN_USER); the su --pty branch needs a password.
# Usage: tests/harness.sh    (prints PASS/FAIL per case; exit 1 on any FAIL)
set -u
SK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
S="$(mktemp -d)"; trap 'chmod -R u+w "$S"; rm -rf "$S"' EXIT
USER_TO_IMPERSONATE="$(id -un)"
export USER_RUN_BASE="$S/base" USER_TO_IMPERSONATE
setp() { python3 -c 'import sys,re;p=sys.argv[1];s=open(p).read();s=re.sub(r"(?s)(<<.PAYLOAD.[^\n]*\n).*?(\nPAYLOAD\n)",lambda m:m.group(1)+"set -euo pipefail\n"+sys.argv[2]+m.group(2),s,count=1);open(p,"w").write(s)' "$1" "$2"; }
r=$("$SK/scaffold.sh" t2) || { echo FAIL scaffold; exit 1; }
failed=0
pass() { echo "PASS $*"; }; fail() { echo "FAIL $*"; failed=1; }

setp "$r" 'echo hi; echo "RESULT a=1" ; echo x > "$ARTIFACTS/x"'
bash "$r" >/dev/null 2>&1; [ "$(cat $USER_RUN_BASE/t2/latest.exit)" = 0 ] && pass success-exit || fail success-exit
"$SK/status.sh" t2 | grep -q 'RESULT a=1' && pass status-result || fail status-result
[ -f $USER_RUN_BASE/t2/artifacts/latest/x ] && pass artifacts-latest || fail artifacts-latest

setp "$r" 'echo bad; false'
bash "$r" >/dev/null 2>&1; rc=$?; [ "$rc" = 1 ] && [ "$(cat $USER_RUN_BASE/t2/latest.exit)" = 1 ] && pass failure-exit || fail "failure-exit rc=$rc"
[ ! -e $USER_RUN_BASE/t2/artifacts/latest/x ] && pass artifacts-fresh-per-run || fail artifacts-fresh-per-run

setp "$r" 'sleep 3; echo done'
bash "$r" >/dev/null 2>&1 & sleep 1
bash "$r" 2>&1 | grep -q 'in progress' && pass concurrent-refused || fail concurrent-refused
"$SK/status.sh" t2 | head -1 | grep -q running && pass status-running || fail status-running
wait

setp "$r" '(sleep 6 >/dev/null 2>&1 &) ; echo spawned'
t0=$SECONDS; bash "$r" >/dev/null 2>&1; dt=$((SECONDS-t0))
flock -n $USER_RUN_BASE/t2/.lock true && pass lock-free-with-detached-child || fail lock-free-with-detached-child
[ $dt -lt 4 ] && pass detached-child-no-hang || fail "detached-child-no-hang dt=$dt"

USER_TO_IMPERSONATE=root bash "$r" </dev/null 2>&1 | grep -q 'needs a terminal' && pass su-needs-tty || fail su-needs-tty
USER_TO_IMPERSONATE=nobody-zz bash "$r" 2>&1 | grep -q 'no such user' && pass bad-user || fail bad-user

setp "$r" 'echo via-sh'
sh "$r" 2>&1 | grep -q via-sh && pass invoked-via-sh || fail invoked-via-sh

setp "$r" 'echo before; sleep 20; echo after'
setsid bash -c "bash '$r'" >"$S/int.out" 2>&1 & pid=$!; sleep 2; kill -INT -- -"$pid"; wait "$pid"; rc=$?
grep -q 'exit=130' "$S/int.out" && [ "$(cat $USER_RUN_BASE/t2/latest.exit 2>/dev/null)" = 130 ] && pass interrupt-recorded || { fail "interrupt-recorded rc=$rc"; tail -3 "$S/int.out"; }
grep -q 'exit=130' "$(readlink -f $USER_RUN_BASE/t2/latest.log)" && pass interrupt-in-log || fail interrupt-in-log

setp "$r" 'echo "PAYLOAD x"; printf "%s\n" "a'"'"'b \$HOME \"q\""'
bash "$r" 2>&1 | grep -q "a'b \$HOME \"q\"" && pass payload-quoting || fail payload-quoting

# cleanup: stale slug dir, old run in live dir, foreign dir, keep, running
b=$USER_RUN_BASE
mkdir -p $b/old-1/artifacts/20200101000000 $b/foreign; echo '# not user-run' > $b/foreign/run.sh; touch -d '30 days ago' $b/foreign/run.sh
printf '#!/usr/bin/env bash\n# user-run: old-1\n' > $b/old-1/run.sh; touch $b/old-1/20200101000000.log
find $b/old-1 -exec touch -h -d '10 days ago' {} +
mkdir -p $b/t2/artifacts/20200101000000; touch $b/t2/20200101000000.log $b/t2/20200101000000.exit $b/t2/payload.20200101000000.sh
touch -d '10 days ago' $b/t2/20200101000000.log
"$SK/cleanup.sh" --dry-run | grep -q "would remove: $b/old-1" && [ -d $b/old-1 ] && pass cleanup-dry-run || fail cleanup-dry-run
"$SK/cleanup.sh" --quiet
[ ! -e $b/old-1 ] && pass cleanup-stale-dir || fail cleanup-stale-dir
[ -e $b/foreign/run.sh ] && pass cleanup-ignores-foreign || fail cleanup-ignores-foreign
[ ! -e $b/t2/20200101000000.log ] && [ ! -e $b/t2/artifacts/20200101000000 ] && [ ! -e $b/t2/payload.20200101000000.sh ] && pass cleanup-old-run || fail cleanup-old-run
[ -e "$(readlink -f $b/t2/latest.log)" ] && [ -x $b/t2/run.sh ] && pass cleanup-keeps-live || fail cleanup-keeps-live
# latest run is kept even when old
id=$(basename "$(readlink $b/t2/latest.log)" .log); touch -d '10 days ago' $b/t2/$id.log
"$SK/cleanup.sh" --quiet; [ -e $b/t2/$id.log ] && pass cleanup-keeps-latest || fail cleanup-keeps-latest
# whole stale dir kept with --keep, and skipped while running
find $b/t2 -exec touch -h -d '10 days ago' {} +
"$SK/cleanup.sh" --quiet --keep t2; [ -d $b/t2 ] && pass cleanup-keep || fail cleanup-keep
exec 8>>$b/t2/.lock; flock 8; "$SK/cleanup.sh" | grep -q 'skip (running): t2' && [ -d $b/t2 ] && pass cleanup-skips-running || fail cleanup-skips-running; exec 8>&-
"$SK/cleanup.sh" --quiet; [ ! -e $b/t2 ] && pass cleanup-stale-live-dir || fail cleanup-stale-live-dir

# ---- pass 2 regressions
r=$("$SK/scaffold.sh" t3); b=$USER_RUN_BASE
setp "$r" 'sleep 3; echo "RESULT edit=1"'
bash "$r" > "$S/edit.out" 2>&1 & pid=$!; sleep 1
python3 -c 'import sys;p=sys.argv[1];s=open(p).read();h,t=s.split("\n",2)[:2],s.split("\n",2)[2];open(p,"w").write("\n".join(h)+"\n"+"#x\n"*7+t.replace("sleep 3","echo NEWPAYLOAD; sleep 1"))' "$r"
wait $pid; rc=$?
[ "$rc" = 0 ] && ! grep -q 'command not found\|unbound' "$S/edit.out" && ! grep -q NEWPAYLOAD "$S/edit.out" && pass edit-during-run || { fail "edit-during-run rc=$rc"; cat "$S/edit.out"; }

chmod 0444 $b/t3/.lock; setp "$r" 'echo ro-lock-ok'
bash "$r" 2>&1 | grep -q ro-lock-ok && pass readonly-lock-file || fail readonly-lock-file; chmod 0664 $b/t3/.lock
rm -f $b/t3/.lock; (umask 022; "$SK/cleanup.sh" --quiet); [ "$(stat -c %a $b/t3/.lock)" = 664 ] && pass cleanup-lock-mode || fail "cleanup-lock-mode $(stat -c %a $b/t3/.lock)"

setp "$r" 'trap "echo cleaned; exit 0" INT; echo waiting; sleep 20 & wait'
setsid bash -c "bash '$r'" >"$S/int2.out" 2>&1 & pid=$!; sleep 2; kill -INT -- -"$pid"; wait "$pid"
grep -q cleaned "$S/int2.out" && [ "$(cat $b/t3/latest.exit)" = 0 ] && pass payload-trap-exit-kept || { fail payload-trap-exit-kept; tail -4 "$S/int2.out"; }
pkill -f '^sleep 20$'

setp "$r" 'printf "RESULT cr=1\r\n"'
bash "$r" >/dev/null 2>&1; "$SK/status.sh" t3 | grep -qx 'RESULT cr=1' && pass result-cr-stripped || fail result-cr-stripped
setp "$r" 'git --no-pager --version >/dev/null; [ "$PAGER" = cat ] && echo "RESULT pager=cat"'
bash "$r" >/dev/null 2>&1; "$SK/status.sh" t3 | grep -q 'RESULT pager=cat' && pass pager-disabled || fail pager-disabled

id=$(basename "$(readlink $b/t3/latest.log)" .log); rm -f $b/t3/$id.started
"$SK/status.sh" t3 | head -1 | grep -q not-started && pass status-not-started || fail status-not-started

setp "$r" 'echo "unterminated'
bash "$r" 2>&1 | grep -q 'syntax error' && pass payload-syntax-refused || fail payload-syntax-refused

"$SK/scaffold.sh" t4 >/dev/null 2>&1; USER_RUN_BASE='/tmp/a$(id)' "$SK/scaffold.sh" t5 2>&1 | grep -q 'bad base' && pass base-whitelist || fail base-whitelist

# unwritable leftovers: reported, marker kept, retried later
mkdir -p $b/t4/artifacts/20200101000000/ro; touch $b/t4/artifacts/20200101000000/ro/f; chmod 0555 $b/t4/artifacts/20200101000000/ro
find $b/t4 -exec touch -h -d '10 days ago' {} +
out=$("$SK/cleanup.sh" --quiet 2>&1); echo "$out" | grep -q 'failed to remove' && [ -f $b/t4/run.sh ] && pass cleanup-partial-reported-marker-kept || { fail cleanup-partial; echo "$out"; }
chmod 0755 $b/t4/artifacts/20200101000000/ro; "$SK/cleanup.sh" --quiet; [ ! -e $b/t4 ] && pass cleanup-retry || fail cleanup-retry

# ---- completion poke (watch.py)
W="python3 $SK/watch.py"; r=$("$SK/scaffold.sh" t6); d=$b/t6
jq_() { python3 -c 'import sys,json;l=sys.stdin.read().split(" ",1);o=json.loads(l[1]);print(eval(sys.argv[1]))' "$1"; }
setp "$r" 'echo hi; echo "RESULT tag=v1"; echo a > "$ARTIFACTS/a.txt"'
$W t6 --timeout 20 --interval 0.2 > "$S/w1.out" & wp=$!; sleep 0.5
bash "$r" >/dev/null 2>&1; wait $wp; wrc=$?
line=$(cat "$S/w1.out")
[ "$wrc" = 0 ] && [ "$(echo "$line" | wc -l)" = 1 ] && [[ "$line" == "USER_RUN_DONE {"* ]] && pass watch-success-one-line || { fail "watch-success-one-line rc=$wrc"; echo "$line"; }
[ "$(echo "$line" | jq_ '(o["state"],o["exit"],o["artifacts"],o["result"],"tail" in o)')" = "('done', 0, 1, ['tag=v1'], False)" ] && pass watch-success-fields || { fail watch-success-fields; echo "$line"; }
id=$(basename "$(readlink $d/latest.log)" .log)
python3 -c 'import json,sys;o=json.load(open(sys.argv[1]));assert o["exit"]==0 and o["started"] is True and o["artifacts"]==1 and o["secs"]>=0' "$d/$id.done.json" && [ ! -e "$d/.$id.done.tmp" ] && pass done-json || fail done-json
$W t6 --timeout 1 --interval 0.2 > "$S/w2.out"; wrc=$?
[ "$wrc" = 3 ] && grep -q '^USER_RUN_WAITING ' "$S/w2.out" && pass watch-no-redeliver-timeout || { fail "watch-no-redeliver-timeout rc=$wrc"; cat "$S/w2.out"; }

setp "$r" 'for i in $(seq 40); do printf "noise %s %0200d\n" $i 0; done; echo "error: boom" >&2; false'
$W t6 --timeout 20 --interval 0.2 > "$S/w3.out" & wp=$!; sleep 0.5
bash "$r" >/dev/null 2>&1; wait $wp
line=$(cat "$S/w3.out")
[ "$(echo "$line" | jq_ '(o["exit"],o["tail"][-1],len(o["tail"])<=10,len("\n".join(o["tail"]).encode())<=1500)')" = "(1, 'error: boom', True, True)" ] && pass watch-failure-tail-bounded || { fail watch-failure-tail-bounded; echo "$line"; }
[ ${#line} -lt 2200 ] && pass watch-failure-line-small || fail "watch-failure-line-small ${#line}"

# A run older than the current payload is not delivered; --latest delivers it anyway.
setp "$r" 'echo stale'; bash "$r" >/dev/null 2>&1; sleep 1.1; setp "$r" 'echo new payload'
$W t6 --timeout 1 --interval 0.2 >/dev/null; [ $? = 3 ] && pass watch-skips-stale-run || fail watch-skips-stale-run
$W t6 --latest --timeout 1 | grep -q '"state":"done"' && pass watch-latest || fail watch-latest

# Ctrl-C: exit 130, from done.json (which lands after .exit).
setp "$r" 'echo before; sleep 20'
$W t6 --timeout 20 --interval 0.2 > "$S/w4.out" & wp=$!; sleep 0.5
setsid bash -c "bash '$r'" >/dev/null 2>&1 & pid=$!; sleep 2; kill -INT -- -"$pid"; wait "$pid"; wait $wp
[ "$(jq_ '(o["exit"],o["secs"]>=1)' < "$S/w4.out")" = "(130, True)" ] && pass watch-interrupt || { fail watch-interrupt; cat "$S/w4.out"; }

# Killed outright (no exit code, lock released): reported as died.
setp "$r" 'echo before-kill; sleep 20'
$W t6 --timeout 20 --interval 0.2 > "$S/w5.out" & wp=$!; sleep 0.5
setsid bash -c "bash '$r'" >/dev/null 2>&1 & pid=$!; sleep 2; kill -KILL -- -"$pid"; wait "$pid" 2>/dev/null; wait $wp
[ "$(jq_ '(o["state"],"exit" in o,"before-kill" in o["tail"])' < "$S/w5.out")" = "('died', False, True)" ] && pass watch-died || { fail watch-died; cat "$S/w5.out"; }
pkill -f '^sleep 20$'

# su failed (no .started): not-started.
setp "$r" 'echo x'; bash "$r" >/dev/null 2>&1; id=$(basename "$(readlink $d/latest.log)" .log); rm -f $d/$id.started
$W t6 --latest --timeout 1 | grep -q '"state":"not-started"' && pass watch-not-started || fail watch-not-started
# Harness scaffolded before done.json (only .exit): still delivered once the lock is free.
setp "$r" 'echo old'; bash "$r" >/dev/null 2>&1; id=$(basename "$(readlink $d/latest.log)" .log); rm -f $d/$id.done.json
$W t6 --timeout 5 --interval 0.2 | grep -q '"state":"done","exit":0' && pass watch-old-harness || fail watch-old-harness
$W nope --timeout 1 2>/dev/null; [ $? = 2 ] && pass watch-bad-slug || fail watch-bad-slug

exit "$failed"
