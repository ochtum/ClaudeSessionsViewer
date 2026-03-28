(() => {
const LANGUAGE_STORAGE_KEY = 'claude_sessions_viewer_language_v1';
const COST_CURRENCY_STORAGE_KEY = 'claude_sessions_viewer_cost_currency_v1';
const COST_SUMMARY_CACHE_KEY = 'claude_sessions_viewer_cost_summary_cache_v1';
const COST_SUMMARY_CACHE_MAX_AGE_MS = 5 * 60 * 1000;
const SUPPORTED_LANGUAGES = ['ja', 'en', 'zh-Hans', 'zh-Hant'];
const SUPPORTED_COST_CURRENCIES = ['USD', 'JPY', 'CNY', 'TWD', 'HKD'];
const COST_I18N = {
  ja: {
    'language.selector': '言語',
    'currency.selector': '通貨',
    'page.title': 'コスト表示 | Claude Sessions Viewer',
    'page.badge': 'Claude Sessions Viewer',
    'page.heroTitle': 'コスト表示',
    'page.heroCopy': '月別・週別・日別の usage 集計を、セッション別と token usage イベント別で見比べられます。',
    'page.refresh': 'Refresh',
    'meta.generatedAt': '更新日時',
    'meta.timeZone': 'タイムゾーン',
    'meta.fxRate': '為替',
    'status.loading': 'コスト集計を読み込み中...',
    'status.error': 'コスト集計の取得に失敗しました。',
    'group.month': '月別',
    'group.week': '週別',
    'group.day': '日別',
    'scope.session': 'セッション別',
    'scope.tokenEvent': 'token usageイベント別',
    'period.two_months_ago': '先々月',
    'period.last_month': '先月',
    'period.this_month': '今月',
    'period.two_weeks_ago': '先々週',
    'period.last_week': '先週',
    'period.this_week': '今週',
    'period.two_days_ago': '一昨日',
    'period.yesterday': '昨日',
    'period.today': '今日',
    'column.period': '期間',
    'column.items': '件数',
    'column.input': 'input',
    'column.cacheCreate': 'cache create',
    'column.cacheRead': 'cache read',
    'column.output': 'output',
    'column.total': 'total',
    'column.cost': 'cost',
    'column.perDollar': '1ドルあたり',
    'column.score': 'score',
    'column.rank': 'rank',
    'usage.costUnknown': 'コスト不明',
    'usage.tokensUnit': 'トークン',
  },
  en: {
    'language.selector': 'Language',
    'currency.selector': 'Currency',
    'page.title': 'Cost Summary | Claude Sessions Viewer',
    'page.badge': 'Claude Sessions Viewer',
    'page.heroTitle': 'Cost Summary',
    'page.heroCopy': 'Compare monthly, weekly, and daily usage totals with session-based and token-usage-event-based views.',
    'page.refresh': 'Refresh',
    'meta.generatedAt': 'Updated',
    'meta.timeZone': 'Time zone',
    'meta.fxRate': 'FX',
    'status.loading': 'Loading cost summary...',
    'status.error': 'Failed to load the cost summary.',
    'group.month': 'Monthly',
    'group.week': 'Weekly',
    'group.day': 'Daily',
    'scope.session': 'Session-based',
    'scope.tokenEvent': 'Token usage events',
    'period.two_months_ago': '2 months ago',
    'period.last_month': 'Last month',
    'period.this_month': 'This month',
    'period.two_weeks_ago': '2 weeks ago',
    'period.last_week': 'Last week',
    'period.this_week': 'This week',
    'period.two_days_ago': '2 days ago',
    'period.yesterday': 'Yesterday',
    'period.today': 'Today',
    'column.period': 'Period',
    'column.items': 'Items',
    'column.input': 'input',
    'column.cacheCreate': 'cache create',
    'column.cacheRead': 'cache read',
    'column.output': 'output',
    'column.total': 'total',
    'column.cost': 'cost',
    'column.perDollar': 'per $1',
    'column.score': 'score',
    'column.rank': 'rank',
    'usage.costUnknown': 'cost unavailable',
    'usage.tokensUnit': 'tokens',
  },
  'zh-Hans': {
    'language.selector': '语言',
    'currency.selector': '货币',
    'page.title': '成本汇总 | Claude Sessions Viewer',
    'page.badge': 'Claude Sessions Viewer',
    'page.heroTitle': '成本汇总',
    'page.heroCopy': '可按月、周、日查看 usage 汇总，并对比“按会话”和“按 token usage 事件”两种视角。',
    'page.refresh': 'Refresh',
    'meta.generatedAt': '更新时间',
    'meta.timeZone': '时区',
    'meta.fxRate': '汇率',
    'status.loading': '正在加载成本汇总...',
    'status.error': '获取成本汇总失败。',
    'group.month': '按月',
    'group.week': '按周',
    'group.day': '按日',
    'scope.session': '按会话',
    'scope.tokenEvent': '按 token usage 事件',
    'period.two_months_ago': '前前月',
    'period.last_month': '上月',
    'period.this_month': '本月',
    'period.two_weeks_ago': '前前周',
    'period.last_week': '上周',
    'period.this_week': '本周',
    'period.two_days_ago': '前天',
    'period.yesterday': '昨天',
    'period.today': '今天',
    'column.period': '期间',
    'column.items': '数量',
    'column.input': 'input',
    'column.cacheCreate': 'cache create',
    'column.cacheRead': 'cache read',
    'column.output': 'output',
    'column.total': 'total',
    'column.cost': 'cost',
    'column.perDollar': '每 $1',
    'column.score': 'score',
    'column.rank': 'rank',
    'usage.costUnknown': '成本未知',
    'usage.tokensUnit': 'tokens',
  },
};
COST_I18N['zh-Hant'] = {
  ...COST_I18N['zh-Hans'],
  'language.selector': '語言',
  'currency.selector': '幣別',
  'page.title': '成本彙總 | Claude Sessions Viewer',
  'page.heroTitle': '成本彙總',
  'page.heroCopy': '可按月、週、日查看 usage 彙總，並比較「按工作階段」與「按 token usage 事件」兩種視角。',
  'meta.generatedAt': '更新時間',
  'meta.timeZone': '時區',
  'meta.fxRate': '匯率',
  'status.loading': '正在載入成本彙總...',
  'status.error': '取得成本彙總失敗。',
  'group.month': '按月',
  'group.week': '按週',
  'group.day': '按日',
  'scope.session': '按工作階段',
  'scope.tokenEvent': '按 token usage 事件',
  'period.two_weeks_ago': '前前週',
  'period.last_week': '上週',
  'period.this_week': '本週',
  'column.period': '期間',
  'column.items': '數量',
};

let uiLanguage = 'ja';
let selectedCostCurrency = 'USD';
let costSummaryData = null;

function normalizeLanguage(value){
  const raw = (value || '').trim();
  if(raw === 'zh' || raw === 'zh-CN' || raw === 'zh-SG'){
    return 'zh-Hans';
  }
  if(raw === 'zh-TW' || raw === 'zh-HK' || raw === 'zh-MO'){
    return 'zh-Hant';
  }
  return SUPPORTED_LANGUAGES.includes(raw) ? raw : 'ja';
}

function normalizeCostCurrency(value){
  const raw = (value || '').trim().toUpperCase();
  return SUPPORTED_COST_CURRENCIES.includes(raw) ? raw : '';
}

function getDefaultCostCurrencyForLanguage(language){
  const normalized = normalizeLanguage(language);
  if(normalized === 'ja'){
    return 'JPY';
  }
  if(normalized === 'zh-Hans'){
    return 'CNY';
  }
  if(normalized === 'zh-Hant'){
    return 'TWD';
  }
  return 'USD';
}

function t(key){
  return (COST_I18N[uiLanguage] && COST_I18N[uiLanguage][key])
    || COST_I18N.ja[key]
    || key;
}

function esc(value){
  return (value ?? '').toString().replace(/[&<>\"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', '\'': '&#39;' }[ch]));
}

function isCostsPage(){
  return !!document.getElementById('costs_groups') && !!document.getElementById('refresh_costs');
}

function getUiLocale(){
  if(uiLanguage === 'zh-Hans') return 'zh-CN';
  if(uiLanguage === 'zh-Hant') return 'zh-TW';
  return uiLanguage || 'ja';
}

function formatNumber(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return '-';
  }
  return numeric.toLocaleString(getUiLocale());
}

function formatCompactNumber(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return '-';
  }
  return new Intl.NumberFormat(getUiLocale(), {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(numeric);
}

function formatUsd(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return t('usage.costUnknown');
  }
  const digits = numeric >= 10 ? 2 : (numeric >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(numeric);
}

function formatJpy(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return '';
  }
  const digits = numeric >= 100 ? 0 : (numeric >= 10 ? 1 : (numeric >= 1 ? 2 : 3));
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'JPY',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(numeric);
}

function formatCny(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return '';
  }
  const digits = numeric >= 10 ? 2 : (numeric >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(numeric);
}

function formatLocalCurrency(value, currencyCode){
  const numeric = Number(value);
  if(!Number.isFinite(numeric) || !currencyCode || currencyCode === 'USD'){
    return '';
  }
  if(currencyCode === 'JPY'){
    return formatJpy(numeric);
  }
  if(currencyCode === 'CNY'){
    return formatCny(numeric);
  }
  const digits = numeric >= 10 ? 2 : (numeric >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: currencyCode,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(numeric);
}

function getPreferredCostCurrencyCode(){
  return normalizeCostCurrency(selectedCostCurrency) || 'USD';
}

function getExchangeRateValue(exchangeRate, currencyCode){
  if(!exchangeRate || currencyCode === 'USD'){
    return null;
  }
  const rate = currencyCode === 'JPY'
    ? Number(exchangeRate.jpy_rate)
    : currencyCode === 'CNY'
      ? Number(exchangeRate.cny_rate)
      : currencyCode === 'TWD'
        ? Number(exchangeRate.twd_rate)
        : currencyCode === 'HKD'
          ? Number(exchangeRate.hkd_rate)
          : NaN;
  return Number.isFinite(rate) && rate > 0 ? rate : null;
}

function convertUsdToLocalCurrency(value, exchangeRate, currencyCode){
  const usd = Number(value);
  const rate = getExchangeRateValue(exchangeRate, currencyCode);
  if(!Number.isFinite(usd) || !Number.isFinite(rate) || rate <= 0){
    return null;
  }
  return usd * rate;
}

function formatCostDisplay(value, exchangeRate){
  if(!(typeof value === 'number' && Number.isFinite(value))){
    return t('usage.costUnknown');
  }
  const currencyCode = getPreferredCostCurrencyCode();
  if(currencyCode === 'USD'){
    return formatUsd(value);
  }
  const localValue = convertUsdToLocalCurrency(value, exchangeRate, currencyCode);
  if(localValue == null){
    return formatUsd(value);
  }
  const formattedLocal = formatLocalCurrency(localValue, currencyCode);
  if(!formattedLocal){
    return formatUsd(value);
  }
  return `${formatUsd(value)} / ${formattedLocal}`;
}

function formatExchangeRateDisplay(exchangeRate){
  const currencyCode = getPreferredCostCurrencyCode();
  if(!exchangeRate || currencyCode === 'USD'){
    return '';
  }
  const rate = getExchangeRateValue(exchangeRate, currencyCode);
  if(!Number.isFinite(rate) || rate <= 0){
    return '';
  }
  const pair = `${exchangeRate.base_currency || 'USD'}/${currencyCode}`;
  const formattedRate = new Intl.NumberFormat(getUiLocale(), {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(rate);
  const fetchedAt = exchangeRate.fetched_at ? formatTimestamp(exchangeRate.fetched_at) : '';
  return fetchedAt
    ? `${pair} ${formattedRate} @ ${fetchedAt}`
    : `${pair} ${formattedRate}`;
}

function formatPeriodCostDisplay(period){
  if(!period || Number(period.item_count || 0) === 0){
    return '-';
  }
  return typeof period.cost_usd === 'number' && Number.isFinite(period.cost_usd)
    ? formatCostDisplay(period.cost_usd, costSummaryData && costSummaryData.exchange_rate)
    : t('usage.costUnknown');
}

function formatTokensPerDollar(totalTokens, costUsd){
  const total = Number(totalTokens);
  const cost = Number(costUsd);
  if(!Number.isFinite(total) || total <= 0 || !Number.isFinite(cost) || cost < 0){
    return '';
  }
  if(cost === 0){
    return '∞';
  }
  return `${formatCompactNumber(total / cost)} ${t('usage.tokensUnit')}`;
}

function getUsageCostPerformance(totalTokens, costUsd){
  const total = Number(totalTokens);
  const cost = Number(costUsd);
  if(!Number.isFinite(total) || total <= 0 || !Number.isFinite(cost) || cost < 0){
    return { score: null, rank: '' };
  }
  if(cost === 0){
    return { score: Number.POSITIVE_INFINITY, rank: 'SS' };
  }
  const tokensPerDollar = total / cost;
  if(!Number.isFinite(tokensPerDollar) || tokensPerDollar <= 0){
    return { score: null, rank: '' };
  }
  const score = Math.log10(tokensPerDollar);
  if(!Number.isFinite(score)){
    return { score: null, rank: '' };
  }
  if(score >= 6.5) return { score, rank: 'SS' };
  if(score >= 6.0) return { score, rank: 'S' };
  if(score >= 5.5) return { score, rank: 'A' };
  if(score >= 5.0) return { score, rank: 'B' };
  if(score >= 4.5) return { score, rank: 'C' };
  if(score >= 4.0) return { score, rank: 'D' };
  return { score, rank: 'E' };
}

function formatUsageScoreDisplay(value){
  const score = Number(value);
  if(score === Number.POSITIVE_INFINITY){
    return '∞';
  }
  if(!Number.isFinite(score)){
    return '-';
  }
  return score.toFixed(2);
}

function formatTimestamp(value){
  if(!value){
    return '-';
  }
  const timestamp = new Date(value);
  if(Number.isNaN(timestamp.getTime())){
    return value;
  }
  return timestamp.toLocaleString(getUiLocale());
}

function setStatus(text, tone){
  const status = document.getElementById('costs_status');
  if(!status){
    return;
  }
  status.textContent = text || '';
  status.classList.toggle('error', tone === 'error');
}

function readCostSummaryCache(){
  try {
    const raw = localStorage.getItem(COST_SUMMARY_CACHE_KEY) || '';
    if(!raw){
      return null;
    }
    const parsed = JSON.parse(raw);
    if(!parsed || typeof parsed !== 'object' || !parsed.data){
      return null;
    }
    const savedAt = Number(parsed.saved_at);
    return {
      data: parsed.data,
      savedAt: Number.isFinite(savedAt) ? savedAt : 0,
    };
  } catch (error) {
    return null;
  }
}

function writeCostSummaryCache(data){
  if(!data){
    return;
  }
  try {
    localStorage.setItem(COST_SUMMARY_CACHE_KEY, JSON.stringify({
      saved_at: Date.now(),
      data,
    }));
  } catch (error) {
    // Ignore storage quota errors and keep the page state only.
  }
}

function isCostSummaryCacheFresh(entry){
  return !!entry
    && Number.isFinite(entry.savedAt)
    && entry.savedAt > 0
    && (Date.now() - entry.savedAt) <= COST_SUMMARY_CACHE_MAX_AGE_MS;
}

function applyCostSummaryData(data){
  costSummaryData = data || null;
  renderMeta();
  renderGroups();
}

function renderMeta(){
  const meta = document.getElementById('costs_meta');
  if(!meta){
    return;
  }
  if(!costSummaryData){
    meta.innerHTML = '';
    return;
  }
  meta.innerHTML = [
    `<div class="costs-meta-item"><span class="costs-meta-label">${esc(t('meta.generatedAt'))}</span><span>${esc(formatTimestamp(costSummaryData.generated_at))}</span></div>`,
    `<div class="costs-meta-item"><span class="costs-meta-label">${esc(t('meta.timeZone'))}</span><span>${esc(costSummaryData.time_zone_id || '-')}</span></div>`,
    costSummaryData.exchange_rate
      ? `<div class="costs-meta-item"><span class="costs-meta-label">${esc(t('meta.fxRate'))}</span><span>${esc(formatExchangeRateDisplay(costSummaryData.exchange_rate))}</span></div>`
      : '',
  ].join('');
}

function renderRank(rank){
  const label = rank || '-';
  if(label === '-'){
    return '<span>-</span>';
  }
  return `<span class="rank-pill rank-${esc(label)}">${esc(label)}</span>`;
}

function renderScopeTable(periods){
  return `<div class="costs-table-wrap"><table class="costs-table"><thead><tr>
    <th>${esc(t('column.period'))}</th>
    <th>${esc(t('column.items'))}</th>
    <th>${esc(t('column.input'))}</th>
    <th>${esc(t('column.cacheCreate'))}</th>
    <th>${esc(t('column.cacheRead'))}</th>
    <th>${esc(t('column.output'))}</th>
    <th>${esc(t('column.total'))}</th>
    <th>${esc(t('column.cost'))}</th>
    <th>${esc(t('column.perDollar'))}</th>
    <th>${esc(t('column.score'))}</th>
    <th>${esc(t('column.rank'))}</th>
  </tr></thead><tbody>${periods.map(period => {
    const performance = getUsageCostPerformance(period.total_tokens || 0, period.cost_usd);
    const cacheCreationTokens = period.cache_creation_tokens ?? period.cache_tokens ?? 0;
    const cacheReadTokens = period.cache_read_tokens || 0;
    return `<tr>
      <td class="costs-period-label">${esc(t(`period.${period.key}`))}</td>
      <td>${esc(formatNumber(period.item_count || 0))}</td>
      <td>${esc(formatNumber(period.input_tokens || 0))}</td>
      <td>${esc(formatNumber(cacheCreationTokens))}</td>
      <td>${esc(formatNumber(cacheReadTokens))}</td>
      <td>${esc(formatNumber(period.output_tokens || 0))}</td>
      <td>${esc(formatNumber(period.total_tokens || 0))}</td>
      <td>${esc(formatPeriodCostDisplay(period))}</td>
      <td>${esc(formatTokensPerDollar(period.total_tokens || 0, period.cost_usd) || '-')}</td>
      <td>${esc(formatUsageScoreDisplay(performance.score))}</td>
      <td>${renderRank(performance.rank)}</td>
    </tr>`;
  }).join('')}</tbody></table></div>`;
}

function renderGroups(){
  const groups = document.getElementById('costs_groups');
  if(!groups){
    return;
  }
  if(!costSummaryData || !Array.isArray(costSummaryData.groups)){
    groups.innerHTML = '';
    return;
  }

  groups.innerHTML = costSummaryData.groups.map(group => {
    return `<section class="costs-group">
      <div class="costs-group-header">
        <div class="costs-group-kicker">Usage Summary</div>
        <div class="costs-group-title">${esc(t(`group.${group.key}`))}</div>
      </div>
      <div class="costs-scope-grid">
        <section class="costs-scope">
          <div class="costs-scope-title">${esc(t('scope.session'))}</div>
          ${renderScopeTable(Array.isArray(group.sessions) ? group.sessions : [])}
        </section>
        <section class="costs-scope">
          <div class="costs-scope-title">${esc(t('scope.tokenEvent'))}</div>
          ${renderScopeTable(Array.isArray(group.token_usage_events) ? group.token_usage_events : [])}
        </section>
      </div>
    </section>`;
  }).join('');
}

function applyLanguage(){
  document.title = t('page.title');
  const languageSelect = document.getElementById('language_select');
  if(languageSelect){
    languageSelect.value = uiLanguage;
    languageSelect.setAttribute('aria-label', t('language.selector'));
  }
  const currencySelect = document.getElementById('currency_select');
  if(currencySelect){
    currencySelect.value = getPreferredCostCurrencyCode();
    currencySelect.setAttribute('aria-label', t('currency.selector'));
  }
  const refresh = document.getElementById('refresh_costs');
  if(refresh){
    refresh.textContent = t('page.refresh');
  }
  const badge = document.getElementById('page_badge');
  if(badge){
    badge.textContent = t('page.badge');
  }
  const title = document.getElementById('page_title');
  if(title){
    title.textContent = t('page.heroTitle');
  }
  const copy = document.getElementById('page_copy');
  if(copy){
    copy.textContent = t('page.heroCopy');
  }
  renderMeta();
  renderGroups();
}

async function loadCostSummary(options){
  const opts = options || {};
  const refresh = document.getElementById('refresh_costs');
  if(refresh){
    refresh.disabled = true;
  }
  if(!opts.silent){
    setStatus(t('status.loading'));
  }
  try {
    const params = new URLSearchParams();
    params.set('ts', Date.now().toString());
    if(opts.forceRefresh){
      params.set('force', '1');
    }
    const response = await fetch(`/api/cost-summary?${params.toString()}`, { cache: 'no-store' });
    const data = await readJsonResponse(response);
    writeCostSummaryCache(data);
    applyCostSummaryData(data);
    setStatus('');
  } catch (error) {
    if(!costSummaryData || opts.clearOnError){
      applyCostSummaryData(null);
    }
    setStatus(t('status.error'), 'error');
  } finally {
    if(refresh){
      refresh.disabled = false;
    }
  }
}

async function readJsonResponse(response){
  const contentType = String(response && response.headers && response.headers.get('content-type') || '').toLowerCase();
  if(contentType.includes('application/json')){
    const data = await response.json();
    if(response.ok){
      return data;
    }
    const message = data && typeof data.error === 'string' && data.error.trim()
      ? data.error.trim()
      : `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`;
    throw new Error(message);
  }

  const text = await response.text();
  const snippet = (text || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  const status = `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`;
  throw new Error(snippet ? `${status}: ${snippet}` : status);
}

function loadInitialLanguage(){
  const params = new URLSearchParams(window.location.search);
  const queryValue = params.get('lang') || '';
  let storedValue = '';
  try {
    storedValue = localStorage.getItem(LANGUAGE_STORAGE_KEY) || '';
  } catch (error) {
    storedValue = '';
  }
  const fromQuery = queryValue ? normalizeLanguage(queryValue) : '';
  const stored = storedValue ? normalizeLanguage(storedValue) : '';
  uiLanguage = fromQuery || stored || normalizeLanguage(navigator.language) || 'ja';
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, uiLanguage);
  } catch (error) {
    // Ignore storage write errors.
  }
}

function loadInitialCostCurrency(){
  const params = new URLSearchParams(window.location.search);
  const queryValue = params.get('currency') || '';
  let storedValue = '';
  try {
    storedValue = localStorage.getItem(COST_CURRENCY_STORAGE_KEY) || '';
  } catch (error) {
    storedValue = '';
  }
  const fromQuery = queryValue ? normalizeCostCurrency(queryValue) : '';
  const stored = storedValue ? normalizeCostCurrency(storedValue) : '';
  selectedCostCurrency = fromQuery || stored || getDefaultCostCurrencyForLanguage(uiLanguage);
  try {
    localStorage.setItem(COST_CURRENCY_STORAGE_KEY, selectedCostCurrency);
  } catch (error) {
    // Ignore storage write errors.
  }
}

function setCostCurrency(nextCurrency, persist){
  selectedCostCurrency = normalizeCostCurrency(nextCurrency) || getDefaultCostCurrencyForLanguage(uiLanguage);
  if(persist !== false){
    try {
      localStorage.setItem(COST_CURRENCY_STORAGE_KEY, selectedCostCurrency);
    } catch (error) {
      // Ignore storage write errors.
    }
  }
  applyLanguage();
}

function initCostsPage(){
  if(!isCostsPage() || window.__claudeCostsPageInitialized){
    return;
  }

  window.__claudeCostsPageInitialized = true;
  loadInitialLanguage();
  loadInitialCostCurrency();
  applyLanguage();

  const languageSelect = document.getElementById('language_select');
  if(languageSelect){
    languageSelect.addEventListener('change', event => {
      uiLanguage = normalizeLanguage(event.target.value);
      try {
        localStorage.setItem(LANGUAGE_STORAGE_KEY, uiLanguage);
      } catch (error) {
        // Ignore storage write errors.
      }
      applyLanguage();
    });
  }

  const currencySelect = document.getElementById('currency_select');
  if(currencySelect){
    currencySelect.addEventListener('change', event => {
      setCostCurrency(event.target.value);
    });
  }

  const refresh = document.getElementById('refresh_costs');
  if(refresh){
    refresh.addEventListener('click', () => {
      void loadCostSummary({ forceRefresh: true });
    });
  }

  window.addEventListener('storage', (event) => {
    if(event.key === LANGUAGE_STORAGE_KEY){
      const nextLanguage = normalizeLanguage(event.newValue || 'ja');
      if(nextLanguage !== uiLanguage){
        uiLanguage = nextLanguage;
        applyLanguage();
      }
      return;
    }
    if(event.key === COST_CURRENCY_STORAGE_KEY){
      const nextCurrency = normalizeCostCurrency(event.newValue || '') || getDefaultCostCurrencyForLanguage(uiLanguage);
      if(nextCurrency !== getPreferredCostCurrencyCode()){
        setCostCurrency(nextCurrency, false);
      }
      return;
    }
    if(event.key === COST_SUMMARY_CACHE_KEY){
      const cached = readCostSummaryCache();
      if(cached && cached.data){
        applyCostSummaryData(cached.data);
        setStatus('');
      }
    }
  });

  const cached = readCostSummaryCache();
  if(cached && cached.data){
    applyCostSummaryData(cached.data);
    setStatus('');
    if(!isCostSummaryCacheFresh(cached)){
      void loadCostSummary({ silent: true });
    }
  } else {
    void loadCostSummary();
  }
}

if(document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', initCostsPage, { once: true });
} else {
  initCostsPage();
}
})();
