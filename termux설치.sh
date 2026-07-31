#!/data/data/com.termux/files/usr/bin/bash
# 한글 이름으로도 실행할 수 있게 남겨 둔 얇은 껍데기.
# 실제 내용은 install.sh 에 있다.
exec bash "$(dirname "$0")/install.sh" "$@"
