#!/usr/bin/env bash
# 기관 리포트 표준 참고문헌 다운로드.
#
# 원격 개발 샌드박스는 아웃바운드 네트워크 정책상 이 호스트들에 접근할 수 없어
# (curl·WebFetch 모두 CONNECT 403) PDF를 직접 받을 수 없다. 이 스크립트는
# 네트워크 제약이 없는 로컬 PC에서 실행한다.
#
# 사용법:
#   bash references/download_references.sh
#
# 각 PDF의 내용과 이 프로젝트에서의 용도는 references/README.md 참고.

set -uo pipefail
cd "$(dirname "$0")"

UA="Mozilla/5.0 (compatible; ReportAgent-refs/1.0)"

# 파일명|URL
DOCS=(
  "gips_standards_for_firms_2020.pdf|https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf"
  "gips_fiduciary_management_handbook.pdf|https://www.gipsstandards.org/wp-content/uploads/2021/06/gips-standards-fmp-handbook.pdf"
  "bridgewater_selection_of_research.pdf|https://www.investmentmagazine.com.au/wp-content/uploads/2021/09/Bridgewater-Research.pdf"
  "bridgewater_daily_observations_sample.pdf|https://economicprinciples.org/downloads/bwam102317.pdf"
  "nps_investment_policy_statement.pdf|https://fund.nps.or.kr/fileDown.do?atchFileId=FL25001964&atchFileSn=1"
  "nps_fund_management_report_2025.pdf|https://www.nps.or.kr/html/download/management/2025_u_report_1.pdf"
  "performance_attribution_equity_portfolios.pdf|https://cran.r-project.org/web/packages/pa/vignettes/pa.pdf"
  "drawdown_from_practice_to_theory.pdf|https://arxiv.org/pdf/1404.7493"
)

ok=0; fail=0
for entry in "${DOCS[@]}"; do
  name="${entry%%|*}"
  url="${entry#*|}"

  if [[ -s "$name" ]]; then
    echo "skip  $name (이미 존재)"
    ok=$((ok+1))
    continue
  fi

  printf 'get   %-52s ' "$name"
  if curl -sSL --fail --max-time 120 -A "$UA" -o "$name.tmp" "$url" 2>/dev/null; then
    # HTML 에러 페이지를 PDF로 착각해 저장하는 것을 막는다.
    if head -c 5 "$name.tmp" | grep -q '%PDF'; then
      mv "$name.tmp" "$name"
      echo "OK ($(du -h "$name" | cut -f1))"
      ok=$((ok+1))
    else
      rm -f "$name.tmp"
      echo "FAIL (PDF가 아님 — 로그인/리다이렉트 페이지일 수 있음)"
      fail=$((fail+1))
    fi
  else
    rm -f "$name.tmp"
    echo "FAIL (다운로드 실패)"
    fail=$((fail+1))
  fi
done

echo
echo "완료: 성공 $ok / 실패 $fail"
[[ $fail -gt 0 ]] && echo "실패한 항목은 references/README.md의 URL로 직접 받으세요(사이트 개편으로 링크가 바뀌었을 수 있음)."
exit 0
