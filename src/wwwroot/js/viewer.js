const state = {
  sessions: [],
  filtered: [],
  labeledSessions: [],
  labeledEvents: [],
  activePath: null,
  activeSession: null,
  activeSessionVersion: '',
  activeExchangeRate: null,
  activeEvents: [],
  activeRawLineCount: 0,
  sessionRoot: '',
  labels: [],
  isSessionsLoading: false,
  isSessionsBackgroundLoading: false,
  hasLoadedSessions: false,
  sessionsTotalCount: 0,
  sessionsError: '',
  sessionsLoadMode: '',
  isLabeledLoading: false,
  hasLoadedLabeled: false,
  labeledError: '',
  isDetailLoading: false,
  detailError: '',
  detailLoadMode: '',
  isEventSelectionMode: false,
  selectedEventIds: new Set(),
  isMessageRangeSelectionMode: false,
  selectedMessageRangeEventId: '',
  detailMessageRangeMode: '',
  leftPaneTab: 'sessions',
};

const FILTER_STORAGE_KEY = 'claude_sessions_viewer_filters_v4';
const LANGUAGE_STORAGE_KEY = 'claude_sessions_viewer_language_v1';
const COST_CURRENCY_STORAGE_KEY = 'claude_sessions_viewer_cost_currency_v1';
const COST_SUMMARY_CACHE_KEY = 'claude_sessions_viewer_cost_summary_cache_v1';
const SESSION_BACKGROUND_APPEND_DELAY_MS = 80;
const COST_SUMMARY_PRELOAD_INTERVAL_MS = 5 * 60 * 1000;
const COST_SUMMARY_CACHE_MAX_AGE_MS = COST_SUMMARY_PRELOAD_INTERVAL_MS;
const fpInstances = {};
const segInstances = {};
const FP_LOCALE_MAP = {
  ja: typeof flatpickr !== 'undefined' && flatpickr.l10ns && flatpickr.l10ns.ja ? flatpickr.l10ns.ja : null,
  en: null,
  'zh-Hans': typeof flatpickr !== 'undefined' && flatpickr.l10ns && flatpickr.l10ns.zh ? flatpickr.l10ns.zh : null,
  'zh-Hant': typeof flatpickr !== 'undefined' && flatpickr.l10ns && flatpickr.l10ns.zh_tw ? flatpickr.l10ns.zh_tw : null,
};
function getFpLocale(){
  return FP_LOCALE_MAP[uiLanguage] || 'default';
}
function buildFpExtraActions(opts){
  const wrap = document.createElement('div');
  wrap.className = 'flatpickr-extra-actions';
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'flatpickr-action flatpickr-action-danger';
  clearBtn.textContent = t('calendar.clear');
  clearBtn.addEventListener('click', () => {
    if(opts.onClear) opts.onClear();
  });
  const todayBtn = document.createElement('button');
  todayBtn.type = 'button';
  todayBtn.className = 'flatpickr-action flatpickr-action-secondary';
  todayBtn.textContent = t('calendar.today');
  todayBtn.addEventListener('click', () => {
    if(opts.onToday) opts.onToday();
  });
  wrap.appendChild(clearBtn);
  wrap.appendChild(todayBtn);
  return wrap;
}
const CAL_SVG = '<svg viewBox="0 0 16 16"><path d="M4.5 1a.5.5 0 0 1 .5.5V3h6V1.5a.5.5 0 0 1 1 0V3h1.5A1.5 1.5 0 0 1 15 4.5v9a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 1 13.5v-9A1.5 1.5 0 0 1 2.5 3H4V1.5a.5.5 0 0 1 .5-.5zM14 7H2v6.5a.5.5 0 0 0 .5.5h11a.5.5 0 0 0 .5-.5V7zM2.5 4a.5.5 0 0 0-.5.5V6h12V4.5a.5.5 0 0 0-.5-.5h-11z"/></svg>';
function createSegInput(cls, maxLen, ph){
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.className = 'seg ' + cls;
  inp.maxLength = maxLen;
  inp.placeholder = ph;
  inp.setAttribute('inputmode', 'numeric');
  inp.autocomplete = 'off';
  return inp;
}
function createSegSep(ch){
  const sp = document.createElement('span');
  sp.className = 'seg-sep';
  sp.textContent = ch;
  return sp;
}
function createSegIcon(svgHtml){
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'seg-icon';
  btn.tabIndex = -1;
  btn.innerHTML = svgHtml;
  return btn;
}
function segAutoAdvance(segments, idx){
  const seg = segments[idx];
  if(!seg) return;
  const max = Number(seg.maxLength);
  if(seg.value.length >= max && idx + 1 < segments.length){
    segments[idx + 1].focus();
    segments[idx + 1].select();
  }
}
function segHandleKeydown(segments, idx, e){
  const seg = segments[idx];
  if(e.key === 'ArrowUp' || e.key === 'ArrowDown'){
    e.preventDefault();
    segStepValue(segments, idx, e.key === 'ArrowUp' ? 1 : -1);
    return;
  }
  if(e.key === 'Backspace' && seg.value === '' && idx > 0){
    e.preventDefault();
    segments[idx - 1].focus();
    return;
  }
  if(e.key === 'ArrowLeft' && seg.selectionStart === 0 && idx > 0){
    e.preventDefault();
    segments[idx - 1].focus();
    return;
  }
  if(e.key === 'ArrowRight' && seg.selectionStart >= seg.value.length && idx + 1 < segments.length){
    e.preventDefault();
    segments[idx + 1].focus();
    segments[idx + 1].select();
    return;
  }
}
function segStepValue(segments, idx, delta){
  const seg = segments[idx];
  const max = Number(seg.maxLength);
  let val = parseInt(seg.value, 10);
  if(isNaN(val)) val = 0;
  val += delta;
  if(max === 4){
    if(val < 1900) val = 1900;
    if(val > 2999) val = 2999;
    seg.value = String(val);
  } else if(seg.classList.contains('seg-m')){
    if(val < 1) val = 12;
    if(val > 12) val = 1;
    seg.value = pad2(val);
  } else if(seg.classList.contains('seg-d')){
    if(val < 1) val = 31;
    if(val > 31) val = 1;
    seg.value = pad2(val);
  } else if(seg.classList.contains('seg-h')){
    if(val < 0) val = 23;
    if(val > 23) val = 0;
    seg.value = pad2(val);
  } else if(seg.classList.contains('seg-mi')){
    if(val < 0) val = 59;
    if(val > 59) val = 0;
    seg.value = pad2(val);
  }
  seg.dispatchEvent(new Event('input', { bubbles: true }));
}
function buildSegDate(hiddenId){
  const hidden = document.getElementById(hiddenId);
  if(!hidden) return null;
  const wrap = document.createElement('div');
  wrap.className = 'seg-wrap seg-date-wrap';
  const yInp = createSegInput('seg-y', 4, 'yyyy');
  const sep1 = createSegSep('/');
  const mInp = createSegInput('seg-m', 2, 'mm');
  const sep2 = createSegSep('/');
  const dInp = createSegInput('seg-d', 2, 'dd');
  const icon = createSegIcon(CAL_SVG);
  const segs = [yInp, mInp, dInp];
  wrap.appendChild(yInp);
  wrap.appendChild(sep1);
  wrap.appendChild(mInp);
  wrap.appendChild(sep2);
  wrap.appendChild(dInp);
  wrap.appendChild(icon);
  hidden.parentNode.insertBefore(wrap, hidden);
  wrap.appendChild(hidden);
  function syncToHidden(){
    const y = yInp.value, m = mInp.value, d = dInp.value;
    if(y && m && d){
      const iso = parseDateInputToIso(y + '-' + m + '-' + d);
      hidden.value = iso;
    } else if(!y && !m && !d){
      hidden.value = '';
    }
  }
  function setFromIso(iso){
    if(!iso){ yInp.value = ''; mInp.value = ''; dInp.value = ''; hidden.value = ''; return; }
    const parsed = parseDateInputToIso(iso);
    if(!parsed){ yInp.value = ''; mInp.value = ''; dInp.value = ''; hidden.value = ''; return; }
    const parts = parsed.split('-');
    yInp.value = parts[0]; mInp.value = parts[1]; dInp.value = parts[2];
    hidden.value = parsed;
  }
  function getValue(){
    const y = yInp.value, m = mInp.value, d = dInp.value;
    if(y && m && d) syncToHidden();
    return hidden.value;
  }
  segs.forEach((seg, i) => {
    seg.addEventListener('input', () => {
      seg.value = seg.value.replace(/[^0-9]/g, '');
      segAutoAdvance(segs, i);
      syncToHidden();
    });
    seg.addEventListener('keydown', (e) => segHandleKeydown(segs, i, e));
    seg.addEventListener('focus', () => seg.select());
    seg.addEventListener('blur', () => {
      if(!seg.value) return;
      const max = Number(seg.maxLength);
      if(max === 4){
        seg.value = seg.value.padStart(4, '0');
      } else {
        seg.value = pad2(parseInt(seg.value, 10) || 0);
      }
      syncToHidden();
    });
  });
  wrap.addEventListener('paste', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const text = e.clipboardData ? e.clipboardData.getData('text') : '';
    const iso = parseDateInputToIso(text);
    if(iso){
      setFromIso(iso);
      hidden.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  if(hidden.value) setFromIso(hidden.value);
  const inst = { wrap, segs, hidden, icon, setFromIso, getValue, syncToHidden };
  segInstances[hiddenId] = inst;
  return inst;
}
function buildSegTime(hiddenId){
  const hidden = document.getElementById(hiddenId);
  if(!hidden) return null;
  const wrap = document.createElement('div');
  wrap.className = 'seg-wrap seg-time-wrap';
  const hInp = createSegInput('seg-h', 2, 'hh');
  const sep = createSegSep(':');
  const miInp = createSegInput('seg-mi', 2, 'mm');
  const segs = [hInp, miInp];
  wrap.appendChild(hInp);
  wrap.appendChild(sep);
  wrap.appendChild(miInp);
  const spin = document.createElement('div');
  spin.className = 'seg-spin';
  const upBtn = document.createElement('button');
  upBtn.type = 'button';
  upBtn.tabIndex = -1;
  upBtn.innerHTML = '<svg viewBox="0 0 10 6"><path d="M0 6L5 0 10 6z"/></svg>';
  const downBtn = document.createElement('button');
  downBtn.type = 'button';
  downBtn.tabIndex = -1;
  downBtn.innerHTML = '<svg viewBox="0 0 10 6"><path d="M0 0L5 6 10 0z"/></svg>';
  spin.appendChild(upBtn);
  spin.appendChild(downBtn);
  wrap.appendChild(spin);
  hidden.parentNode.insertBefore(wrap, hidden);
  wrap.appendChild(hidden);
  let lastFocusedSeg = hInp;
  function syncToHidden(){
    const h = hInp.value, mi = miInp.value;
    if(h && mi){
      hidden.value = parseTimeInputToValue(h + ':' + mi);
    } else if(!h && !mi){
      hidden.value = '';
    }
  }
  function setFromValue(val){
    if(!val){ hInp.value = ''; miInp.value = ''; hidden.value = ''; return; }
    const parsed = parseTimeInputToValue(val);
    if(!parsed){ hInp.value = ''; miInp.value = ''; hidden.value = ''; return; }
    const parts = parsed.split(':');
    hInp.value = parts[0]; miInp.value = parts[1];
    hidden.value = parsed;
  }
  function getValue(){
    const h = hInp.value, mi = miInp.value;
    if(h && mi) syncToHidden();
    return hidden.value;
  }
  function stepFocused(delta){
    if(hidden.disabled || wrap.classList.contains('disabled')){
      return;
    }
    const seg = lastFocusedSeg;
    const idx = segs.indexOf(seg);
    if(idx < 0) return;
    if(seg.disabled){
      return;
    }
    let val = parseInt(seg.value, 10);
    if(isNaN(val)) val = 0;
    val += delta;
    if(seg.classList.contains('seg-h')){
      if(val < 0) val = 23;
      if(val > 23) val = 0;
    } else {
      if(val < 0) val = 59;
      if(val > 59) val = 0;
    }
    seg.value = pad2(val);
    syncToHidden();
  }
  upBtn.addEventListener('click', () => {
    if(upBtn.disabled || hidden.disabled || wrap.classList.contains('disabled')) return;
    stepFocused(1);
    hidden.dispatchEvent(new Event('change', { bubbles: true }));
  });
  downBtn.addEventListener('click', () => {
    if(downBtn.disabled || hidden.disabled || wrap.classList.contains('disabled')) return;
    stepFocused(-1);
    hidden.dispatchEvent(new Event('change', { bubbles: true }));
  });
  segs.forEach((seg, i) => {
    seg.addEventListener('input', () => {
      seg.value = seg.value.replace(/[^0-9]/g, '');
      segAutoAdvance(segs, i);
      syncToHidden();
    });
    seg.addEventListener('keydown', (e) => segHandleKeydown(segs, i, e));
    seg.addEventListener('focus', () => { seg.select(); lastFocusedSeg = seg; });
    seg.addEventListener('blur', () => {
      if(!seg.value) return;
      seg.value = pad2(parseInt(seg.value, 10) || 0);
      syncToHidden();
    });
  });
  wrap.addEventListener('paste', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const text = e.clipboardData ? e.clipboardData.getData('text') : '';
    const tv = parseTimeInputToValue(text);
    if(tv){
      setFromValue(tv);
      hidden.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  if(hidden.value) setFromValue(hidden.value);
  const inst = { wrap, segs, hidden, setFromValue, getValue, syncToHidden };
  segInstances[hiddenId] = inst;
  return inst;
}
function initFlatpickrDate(id, onChange){
  const prevValue = parseDateInputToIso(getFpDateValue(id));
  destroyFpInstance(id);
  if(typeof flatpickr === 'undefined') return;
  const hidden = document.getElementById(id);
  if(!hidden) return;
  const seg = segInstances[id];
  if(!seg) return;
  const posEl = seg.wrap;
  const dummy = document.createElement('input');
  dummy.type = 'text';
  dummy.className = 'seg flatpickr-dummy';
  dummy.style.cssText = 'position:absolute;width:0;height:0;opacity:0;pointer-events:none;overflow:hidden;border:0;padding:0;margin:0;';
  seg.wrap.appendChild(dummy);
  const fp = flatpickr(dummy, {
    dateFormat: 'Y-m-d',
    allowInput: false,
    locale: getFpLocale(),
    clickOpens: false,
    positionElement: posEl,
    onReady: function(selectedDates, dateStr, instance){
      const actions = buildFpExtraActions({
        onClear: function(){
          instance.clear();
          seg.setFromIso('');
          instance.close();
          if(onChange) onChange();
        },
        onToday: function(){
          instance.setDate(new Date(), true);
          const d = instance.selectedDates[0];
          seg.setFromIso(d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()));
          instance.close();
          if(onChange) onChange();
        },
      });
      instance.calendarContainer.appendChild(actions);
    },
    onChange: function(selectedDates){
      if(selectedDates.length > 0){
        const d = selectedDates[0];
        seg.setFromIso(d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()));
      }
      if(onChange) onChange();
    },
  });
  // Use a replaceable handler so repeated init does not keep stale flatpickr instances.
  seg.icon.onclick = () => {
    const current = fpInstances[id];
    if(current){
      current.toggle();
    }
  };
  seg.segs.forEach(s => {
    s.addEventListener('change', () => { if(onChange) onChange(); });
  });
  if(prevValue){
    fp.setDate(prevValue, false);
    seg.setFromIso(prevValue);
  }
  fpInstances[id] = fp;
}
function initFlatpickrDateTime(dateId, timeId, onChange){
  const prevDate = parseDateInputToIso(getFpDateValue(dateId));
  const timeEl = document.getElementById(timeId);
  const prevTime = timeEl ? parseTimeInputToValue(timeEl.value) : '';
  destroyFpInstance(dateId);
  if(typeof flatpickr === 'undefined') return;
  const hidden = document.getElementById(dateId);
  if(!hidden) return;
  const dateSeg = segInstances[dateId];
  const timeSeg = segInstances[timeId];
  if(!dateSeg) return;
  const posEl = dateSeg.wrap;
  const dummy = document.createElement('input');
  dummy.type = 'text';
  dummy.className = 'seg flatpickr-dummy';
  dummy.style.cssText = 'position:absolute;width:0;height:0;opacity:0;pointer-events:none;overflow:hidden;border:0;padding:0;margin:0;';
  dateSeg.wrap.appendChild(dummy);
  const fp = flatpickr(dummy, {
    dateFormat: 'Y-m-d',
    allowInput: false,
    locale: getFpLocale(),
    clickOpens: false,
    positionElement: posEl,
    onReady: function(selectedDates, dateStr, instance){
      const actions = buildFpExtraActions({
        onClear: function(){
          instance.clear();
          dateSeg.setFromIso('');
          if(timeSeg) timeSeg.setFromValue('');
          else if(timeEl) timeEl.value = '';
          instance.close();
          if(onChange) onChange();
        },
        onToday: function(){
          const now = new Date();
          instance.setDate(now, true);
          dateSeg.setFromIso(now.getFullYear() + '-' + pad2(now.getMonth() + 1) + '-' + pad2(now.getDate()));
          const timeStr = pad2(now.getHours()) + ':' + pad2(now.getMinutes());
          if(timeSeg) timeSeg.setFromValue(timeStr);
          else if(timeEl) timeEl.value = timeStr;
          instance.close();
          if(onChange) onChange();
        },
      });
      instance.calendarContainer.appendChild(actions);
    },
    onChange: function(selectedDates){
      if(selectedDates.length > 0){
        const d = selectedDates[0];
        dateSeg.setFromIso(d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()));
      }
      if(onChange) onChange();
    },
  });
  // Use a replaceable handler so repeated init does not keep stale flatpickr instances.
  dateSeg.icon.onclick = () => {
    const current = fpInstances[dateId];
    if(current){
      current.toggle();
    }
  };
  dateSeg.segs.forEach(s => {
    s.addEventListener('change', () => { if(onChange) onChange(); });
  });
  if(timeSeg){
    timeSeg.segs.forEach(s => {
      s.addEventListener('change', () => { if(onChange) onChange(); });
    });
  }
  if(prevDate){
    fp.setDate(prevDate, false);
    dateSeg.setFromIso(prevDate);
  }
  fpInstances[dateId] = fp;
}
function destroyFpInstance(id){
  if(fpInstances[id]){
    const inst = fpInstances[id];
    const dummy = inst.element;
    inst.destroy();
    if(dummy && dummy.classList.contains('flatpickr-dummy') && dummy.parentNode){
      dummy.parentNode.removeChild(dummy);
    }
    delete fpInstances[id];
  }
}
function destroyAllFpInstances(){
  Object.keys(fpInstances).forEach(destroyFpInstance);
}
function setFpDateValue(id, value){
  const seg = segInstances[id];
  if(seg){
    seg.setFromIso(value || '');
  }
  const fp = fpInstances[id];
  if(fp){
    fp.setDate(value || null, false);
  } else if(!seg){
    const el = document.getElementById(id);
    if(el) el.value = value || '';
  }
}
function combineDateTimeStr(dateStr, timeStr){
  if(!dateStr) return '';
  return timeStr ? dateStr + ' ' + timeStr : dateStr;
}
function setFpDateTimeValue(dateId, timeId, dateVal, timeVal){
  const dateSeg = segInstances[dateId];
  const timeSeg = segInstances[timeId];
  if(dateSeg) dateSeg.setFromIso(dateVal || '');
  if(timeSeg) timeSeg.setFromValue(timeVal || '');
  const fp = fpInstances[dateId];
  if(fp){
    fp.setDate(dateVal || null, false);
  } else if(!dateSeg){
    const el = document.getElementById(dateId);
    if(el) el.value = dateVal || '';
  }
  if(!timeSeg){
    const timeEl = document.getElementById(timeId);
    if(timeEl) timeEl.value = timeVal || '';
  }
}
function clearFpInstance(id){
  const seg = segInstances[id];
  if(seg){
    if(seg.setFromIso) seg.setFromIso('');
    else if(seg.setFromValue) seg.setFromValue('');
  }
  const fp = fpInstances[id];
  if(fp){
    fp.clear();
  } else if(!seg){
    const el = document.getElementById(id);
    if(el) el.value = '';
  }
}
function getFpDateValue(id){
  const seg = segInstances[id];
  if(seg && seg.getValue){
    return seg.getValue();
  }
  const fp = fpInstances[id];
  if(fp && fp.selectedDates.length > 0){
    const d = fp.selectedDates[0];
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }
  const el = document.getElementById(id);
  return el ? el.value : '';
}
function initSegmentedInputs(){
  buildSegDate('date_from');
  buildSegDate('date_to');
  buildSegDate('event_date_from_date');
  buildSegTime('event_date_from_time');
  buildSegDate('event_date_to_date');
  buildSegTime('event_date_to_time');
  buildSegDate('detail_event_date_from_date');
  buildSegTime('detail_event_date_from_time');
  buildSegDate('detail_event_date_to_date');
  buildSegTime('detail_event_date_to_time');
}
function initAllFlatpickr(){
  initFlatpickrDate('date_from', applyFilter);
  initFlatpickrDate('date_to', applyFilter);
  initFlatpickrDateTime('event_date_from_date', 'event_date_from_time', applyFilter);
  initFlatpickrDateTime('event_date_to_date', 'event_date_to_time', applyFilter);
  initFlatpickrDateTime('detail_event_date_from_date', 'detail_event_date_from_time', function(){
    saveFilters();
    renderActiveSession();
  });
  initFlatpickrDateTime('detail_event_date_to_date', 'detail_event_date_to_time', function(){
    saveFilters();
    renderActiveSession();
  });
}
const SUPPORTED_LANGUAGES = ['ja', 'en', 'zh-Hans', 'zh-Hant'];
const SUPPORTED_COST_CURRENCIES = ['USD', 'JPY', 'CNY', 'TWD', 'HKD'];
const I18N = {
  ja: {
    'language.selector': '言語',
    'currency.selector': '通貨',
    'header.subtitle': 'ClaudeCodeのイベント履歴を一覧表示・詳細表示して、検索できます。\n覚えておきたい内容にラベルを付けて、あとから見つけることもできます。',
    'header.shortcuts': 'ショートカット',
    'header.meta.show': 'メタ表示',
    'header.meta.hide': 'メタ非表示',
    'header.list.hide': 'セッション一覧を隠す',
    'header.list.show': 'セッション一覧を表示',
    'header.list.hideShort': '一覧を隠す',
    'header.list.showShort': '一覧を表示',
    'header.labels': 'ラベル管理',
    'header.costs': 'コスト表示',
    'leftPane.tab.sessions': 'セッションリスト',
    'leftPane.tab.labels': 'ラベルリスト',
    'labelsPane.kicker': 'Label Browser',
    'labelsPane.heading': 'ラベルリスト',
    'labelsPane.copy': 'ラベルを付けたセッションとイベントをここからまとめて開けます。',
    'labelsPane.group.sessions': 'ラベル付きセッション',
    'labelsPane.group.events': 'ラベル付きイベント',
    'labelsPane.item.session': 'session',
    'labelsPane.item.event': 'event',
    'labelsPane.kind.message': 'message',
    'labelsPane.kind.function_call': 'function call',
    'labelsPane.kind.function_output': 'function output',
    'labelsPane.kind.agent_update': 'agent update',
    'labelsPane.kind.token_usage': 'token usage',
    'todayUsage.title': '今日',
    'todayUsage.loading': '今日の usage を読み込み中...',
    'todayUsage.empty': '今日の usage はまだありません',
    'todayUsage.error': '今日の usage を取得できませんでした',
    'todaySummary.label': '今日',
    'todaySummary.loading': '今日の usage を読み込み中...',
    'todaySummary.empty': '今日の token usage はまだありません。',
    'todaySummary.error': '今日の usage の取得に失敗しました。',
    'todaySummary.refreshing': '更新中...',
    'todaySummary.scope.tokenEvent': 'token usage',
    'todaySummary.scope.session': 'session',
    'toolbar.kicker': 'Session Browser',
    'toolbar.heading': '検索と絞り込み',
    'toolbar.copy': 'フィルターは次回起動時にも保持されます。',
    'toolbar.reload': 'Reload',
    'toolbar.clear': 'Clear',
    'toolbar.filters.hide': 'フィルタを隠す',
    'toolbar.filters.show': 'フィルタを表示',
    'search.title': '検索',
    'search.copy': 'cwd とキーワードで候補を先に絞り込みます。',
    'search.cwd': '作業ディレクトリ',
    'search.keyword': 'キーワード',
    'search.mode': '条件',
    'filter.title': 'フィルター',
    'filter.copy': '期間・source・ラベルで一覧を整理します。',
    'filter.dateFrom': '開始日',
    'filter.dateTo': '終了日',
    'filter.eventDateFrom': 'イベント開始日時',
    'filter.eventDateTo': 'イベント終了日時',
    'common.date': '日付',
    'common.time': '時間',
    'filter.source': 'source',
    'filter.subagents': 'subagents',
    'filter.sessionLabel': 'セッションラベル',
    'filter.eventLabel': 'イベントラベル',
    'filter.source.all': 'source: all',
    'filter.source.cli': 'source: Claude Code CLI',
    'filter.source.vscode': 'source: Claude Desktop',
    'filter.subagents.include': '含む',
    'filter.subagents.exclude': '含まない',
    'filter.sessionLabel.all': 'session label: all',
    'filter.eventLabel.all': 'event label: all',
    'filter.sort': '並び順',
    'filter.sort.desc': '新しい順',
    'filter.sort.asc': '古い順',
    'filter.sort.updated': '最終更新日時順',
    'filter.mode.and': 'keyword AND',
    'filter.mode.or': 'keyword OR',
    'placeholder.cwd': 'cwd (部分一致)',
    'placeholder.keyword': 'keyword filter',
    'placeholder.detailKeyword': 'detail keyword',
    'detail.display': '表示',
    'detail.toggle.user': 'ユーザー指示のみ表示',
    'detail.toggle.ai': 'AIレスポンスのみ表示',
    'detail.toggle.turn': '各入力と最終応答のみ',
    'detail.toggle.tokenUsage': 'token usageのみ表示',
    'detail.toggle.reverse': '表示順を逆にする',
    'detail.toggle.selectedOnly': '選択イベントのみ表示',
    'detail.label': 'ラベルフィルター',
    'detail.label.all': 'all',
    'detail.costSort': 'コストソート',
    'detail.costSort.total': 'トークントータル順',
    'detail.costSort.cost': 'コスト順',
    'detail.costSort.score': 'score順',
    'detail.refresh': 'Refresh',
    'detail.refreshing': 'Refreshing...',
    'detail.clear': 'Clear',
    'detail.actions.hide': '詳細操作を隠す',
    'detail.actions.show': '詳細操作を表示',
    'detail.actions': '操作',
    'detail.copyResume': 'セッション再開コマンドコピー',
    'detail.addSessionLabel': 'セッションにラベル追加',
    'detail.copyDisplayed': '表示中メッセージコピー',
    'detail.selectMode': '選択モード',
    'detail.selectEnd': '選択終了',
    'detail.copySelected': '選択コピー',
    'detail.copySelectedCount': '選択コピー ({count}件)',
    'detail.search': '検索',
    'detail.searchKeyword': '詳細キーワード',
    'detail.searchFilter': 'フィルター',
    'detail.searchFilterClear': 'フィルター解除',
    'detail.searchRun': '検索',
    'detail.prev': '前へ',
    'detail.next': '次へ',
    'detail.matchCounter': '{current} / {total}',
    'detail.searchClear': '検索をクリア',
    'detail.eventDateFrom': 'イベント開始日時',
    'detail.eventDateTo': 'イベント終了日時',
    'detail.eventDateClear': '日時クリア',
    'detail.range': '範囲選択',
    'detail.rangeMode': '起点選択モード',
    'detail.rangeModeEnd': '起点選択終了',
    'detail.rangeClear': '起点解除',
    'detail.rangeAfter': '起点以降のみ表示',
    'detail.rangeAfterActive': '起点以降のみ表示中',
    'detail.rangeBefore': '起点以前のみ表示',
    'detail.rangeBeforeActive': '起点以前のみ表示中',
    'detail.bodyExpand': '▼ 続きを表示',
    'detail.bodyCollapse': '▲ 折りたたむ',
    'calendar.clear': '削除',
    'calendar.today': '今日',
    'session.labels.empty': 'セッションラベルはまだありません',
    'session.labels.loading': 'セッションラベルを読み込み中...',
    'shortcut.title': 'ショートカット',
    'shortcut.copy': '入力欄にカーソルがある間は実行されません。Esc で閉じるか、検索入力からカーソルを外せます。',
    'shortcut.close': '閉じる',
    'shortcut.refresh': '表示中の一覧またはセッション詳細を更新',
    'shortcut.toggleFilters': '左ペインのフィルタ表示を切り替え',
    'shortcut.clearList': '左ペインの Clear を実行',
    'shortcut.focusSearch': '検索入力欄にフォーカス',
    'shortcut.nextMatch': '詳細検索の次のヒットへ移動',
    'shortcut.prevMatch': '詳細検索の前のヒットへ移動',
    'shortcut.meta': 'path / cwd / time のメタ表示を切り替え',
    'shortcut.prevSession': '前のセッションを開く',
    'shortcut.nextSession': '次のセッションを開く',
    'shortcut.onlyUser': 'ユーザー指示のみ表示を切り替え',
    'shortcut.onlyAi': 'AIレスポンスのみ表示を切り替え',
    'shortcut.turnBoundary': '各入力と最終応答のみを切り替え',
    'shortcut.tokenUsage': 'token usageのみ表示を切り替え',
    'shortcut.reverse': '表示順を逆にするを切り替え',
    'shortcut.clearDetail': '右ペインの表示条件と操作状態をクリア',
    'shortcut.toggleActions': '詳細操作の表示と非表示を切り替え',
    'shortcut.copyResume': 'セッション再開コマンドをコピー',
    'shortcut.copyDisplayed': '表示中メッセージをコピー',
    'shortcut.toggleSelection': '選択モードの開始と終了を切り替え',
    'shortcut.copySelected': '選択中メッセージをコピー',
    'shortcut.toggleRange': '起点選択モードの開始と終了を切り替え',
    'shortcut.clearRange': '起点を解除',
    'shortcut.before': '起点以前のみ表示',
    'shortcut.after': '起点以降のみ表示',
    'shortcut.escape': 'ショートカット一覧やラベル追加ポップアップを閉じる。検索入力欄からカーソルを外す。',
    'meta.sessionRoot': 'session root',
    'meta.path': 'path',
    'meta.cwd': 'cwd',
    'meta.time': 'time',
    'meta.usage': 'usage',
    'meta.models': 'models',
    'meta.effortLevel': 'Effort Level',
    'meta.fxRate': '為替',
    'meta.status': 'status',
    'usage.model': 'model',
    'usage.input': 'input',
    'usage.cached': 'cache',
    'usage.cacheCreate': 'cache作成',
    'usage.cacheRead': 'cache読込',
    'usage.output': 'output',
    'usage.total': 'total',
    'usage.cost': 'cost',
    'usage.perDollar': '1ドルあたり',
    'usage.score': 'score',
    'usage.rank': 'rank',
    'usage.tooltip.input': 'モデルに送られた入力トークン数です。',
    'usage.tooltip.cached': 'cache作成とcache読込を合計したトークン数です。',
    'usage.tooltip.cacheCreate': 'cache作成に使われたトークン数です。',
    'usage.tooltip.cacheRead': 'cache読込に使われたトークン数です。',
    'usage.tooltip.output': 'モデルが生成した出力トークン数です。',
    'usage.tooltip.total': 'input + cache + output の総トークン数です。',
    'usage.tooltip.cost': 'この usage に記録された概算コスト（USD）です。',
    'usage.tooltip.perDollar': '概算コスト 1 ドルあたりの総トークン数です。',
    'usage.tooltip.score': '総トークン数 / コスト(USD) の常用対数です。1.0 上がるとコスパは 10 倍です。',
    'usage.tooltip.rank': 'score を固定しきい値で SS / S / A / B / C / D / E に分類したランクです。',
    'usage.costUnknown': 'コスト不明',
    'usage.tokensUnit': 'トークン',
    'usage.totalTokens': '合計 {count} tok',
    'usage.inputTokens': '入力 {count}',
    'usage.outputTokens': '出力 {count}',
    'usage.cacheCreateTokens': 'cache作成 {count}',
    'usage.cacheReadTokens': 'cache読込 {count}',
    'usage.costUsd': 'cost {cost}',
    'summary.labels': 'labels: sessions {sessions} / events {events}',
    'summary.sessions': 'sessions: {current} / {filtered} / {total}',
    'summary.events': 'events: {visible}/{total}',
    'summary.eventsLoading': 'events: loading...',
    'summary.raw': 'raw {count}',
    'session.preview.empty': '(previewなし)',
    'status.sessions.loadingTitle': 'セッション一覧を読み込み中...',
    'status.sessions.loadingCopy': '最新のセッションを確認しています。',
    'status.sessions.errorTitle': '一覧の取得に失敗しました',
    'status.sessions.noMatchesTitle': '条件に一致するセッションはありません',
    'status.sessions.noMatchesCopy': 'フィルタ条件を見直すか、Reload を実行してください。',
    'status.sessions.emptyTitle': 'セッションがまだ見つかりません',
    'status.sessions.emptyCopy': '読み込み対象ディレクトリに .jsonl セッションがあるか確認してください。',
    'status.sessions.refreshTitle': '一覧を更新中...',
    'status.sessions.refreshCopy': '最新のセッションを再取得しています。',
    'status.sessions.backgroundTitle': '残りのセッションを読み込み中...',
    'status.sessions.backgroundCopy': '操作は続けたまま、一覧を少しずつ追加しています。',
    'status.sessions.backgroundInline': '追加読込中 {loaded} / {total}',
    'status.labels.loadingTitle': 'ラベルリストを読み込み中...',
    'status.labels.loadingCopy': 'ラベル付きのセッションとイベントを整理しています。',
    'status.labels.errorTitle': 'ラベルリストの取得に失敗しました',
    'status.labels.emptyTitle': 'ラベル付きの項目はまだありません',
    'status.labels.emptyCopy': 'セッションやイベントにラベルを付けると、ここに一覧表示されます。',
    'status.detail.loadingTitle': 'セッション詳細を読み込み中...',
    'status.detail.loadingCopy': 'イベントを取得しています。',
    'status.detail.errorTitle': '詳細の取得に失敗しました',
    'status.detail.selectSession': 'セッションを選択してください',
    'status.detail.noDisplayTitle': '表示できるイベントはありません',
    'status.detail.noDisplayCopy': 'このセッションには表示対象のイベントがありません。',
    'status.detail.noMatchTitle': '条件に一致するイベントはありません',
    'status.detail.noMatchCopy': '表示条件を変更するとイベントが表示される可能性があります。',
    'status.detail.refreshTitle': 'セッション詳細を更新中...',
    'status.detail.refreshCopy': '最新のイベントを再取得しています。',
    'error.sessions': 'セッション一覧の取得に失敗しました',
    'error.detail': 'セッション詳細の取得に失敗しました',
    'picker.noLabels': 'ラベルがありません。先にラベル管理から作成してください。',
    'picker.removeLabel': 'ラベル解除',
    'picker.addLabel': 'ラベル追加',
    'copy.copied': 'コピーしました',
    'copy.displayedCount': '{count}件コピー',
    'copy.selectedCount': '{count}件コピー',
    'copy.single': 'コピー',
  },
  en: {
    'language.selector': 'Language',
    'currency.selector': 'Currency',
    'header.subtitle': 'Browse ClaudeCode event histories in list and detail views, and search them.\nYou can also attach labels to anything worth remembering and find it later.',
    'header.shortcuts': 'Shortcuts',
    'header.meta.show': 'Show meta',
    'header.meta.hide': 'Hide meta',
    'header.list.hide': 'Hide session list',
    'header.list.show': 'Show session list',
    'header.list.hideShort': 'Hide list',
    'header.list.showShort': 'Show list',
    'header.labels': 'Labels',
    'header.costs': 'Costs',
    'leftPane.tab.sessions': 'Session list',
    'leftPane.tab.labels': 'Label list',
    'labelsPane.kicker': 'Label Browser',
    'labelsPane.heading': 'Label list',
    'labelsPane.copy': 'Labeled sessions and events are collected here.',
    'labelsPane.group.sessions': 'Labeled sessions',
    'labelsPane.group.events': 'Labeled events',
    'labelsPane.item.session': 'session',
    'labelsPane.item.event': 'event',
    'labelsPane.kind.message': 'message',
    'labelsPane.kind.function_call': 'function call',
    'labelsPane.kind.function_output': 'function output',
    'labelsPane.kind.agent_update': 'agent update',
    'labelsPane.kind.token_usage': 'token usage',
    'todayUsage.title': 'Today',
    'todayUsage.loading': 'Loading today\'s usage...',
    'todayUsage.empty': 'No usage yet today',
    'todayUsage.error': 'Failed to load today\'s usage',
    'todaySummary.label': 'Today',
    'todaySummary.loading': "Loading today's usage...",
    'todaySummary.empty': 'No token usage recorded for today yet.',
    'todaySummary.error': "Failed to load today's usage.",
    'todaySummary.refreshing': 'Refreshing...',
    'todaySummary.scope.tokenEvent': 'token usage',
    'todaySummary.scope.session': 'session',
    'toolbar.kicker': 'Session Browser',
    'toolbar.heading': 'Search and filter',
    'toolbar.copy': 'Filters are preserved the next time you launch the viewer.',
    'toolbar.reload': 'Reload',
    'toolbar.clear': 'Clear',
    'toolbar.filters.hide': 'Hide filters',
    'toolbar.filters.show': 'Show filters',
    'search.title': 'Search',
    'search.copy': 'Narrow candidates with cwd and keywords first.',
    'search.cwd': 'Working directory',
    'search.keyword': 'Keyword',
    'search.mode': 'Mode',
    'filter.title': 'Filters',
    'filter.copy': 'Organize the list by time range, source, and labels.',
    'filter.dateFrom': 'Start date',
    'filter.dateTo': 'End date',
    'filter.eventDateFrom': 'Event start date/time',
    'filter.eventDateTo': 'Event end date/time',
    'common.date': 'Date',
    'common.time': 'Time',
    'filter.source': 'Source',
    'filter.subagents': 'subagents',
    'filter.sessionLabel': 'Session label',
    'filter.eventLabel': 'Event label',
    'filter.source.all': 'source: all',
    'filter.source.cli': 'source: Claude Code CLI',
    'filter.source.vscode': 'source: Claude Desktop',
    'filter.subagents.include': 'Include',
    'filter.subagents.exclude': 'Exclude',
    'filter.sessionLabel.all': 'session label: all',
    'filter.eventLabel.all': 'event label: all',
    'filter.sort': 'Sort order',
    'filter.sort.desc': 'Newest first',
    'filter.sort.asc': 'Oldest first',
    'filter.sort.updated': 'Last updated',
    'filter.mode.and': 'keyword AND',
    'filter.mode.or': 'keyword OR',
    'placeholder.cwd': 'cwd (partial match)',
    'placeholder.keyword': 'keyword filter',
    'placeholder.detailKeyword': 'detail keyword',
    'detail.display': 'Display',
    'detail.toggle.user': 'Only user instructions',
    'detail.toggle.ai': 'Only AI responses',
    'detail.toggle.turn': 'Only each input and final reply',
    'detail.toggle.tokenUsage': 'Only token usage',
    'detail.toggle.reverse': 'Reverse order',
    'detail.toggle.selectedOnly': 'Selected events only',
    'detail.label': 'Label filter',
    'detail.label.all': 'all',
    'detail.costSort': 'Cost sort',
    'detail.costSort.total': 'Total tokens',
    'detail.costSort.cost': 'Cost',
    'detail.costSort.score': 'Score',
    'detail.refresh': 'Refresh',
    'detail.refreshing': 'Refreshing...',
    'detail.clear': 'Clear',
    'detail.actions.hide': 'Hide detail actions',
    'detail.actions.show': 'Show detail actions',
    'detail.actions': 'Actions',
    'detail.copyResume': 'Copy resume command',
    'detail.addSessionLabel': 'Add session label',
    'detail.copyDisplayed': 'Copy displayed messages',
    'detail.selectMode': 'Selection mode',
    'detail.selectEnd': 'End selection',
    'detail.copySelected': 'Copy selected',
    'detail.copySelectedCount': 'Copy selected ({count})',
    'detail.search': 'Search',
    'detail.searchKeyword': 'Detail keyword',
    'detail.searchFilter': 'Filter',
    'detail.searchFilterClear': 'Clear filter',
    'detail.searchRun': 'Search',
    'detail.prev': 'Prev',
    'detail.next': 'Next',
    'detail.matchCounter': '{current} / {total}',
    'detail.searchClear': 'Clear search',
    'detail.eventDateFrom': 'Event start date/time',
    'detail.eventDateTo': 'Event end date/time',
    'detail.eventDateClear': 'Clear dates',
    'detail.range': 'Range',
    'detail.rangeMode': 'Anchor mode',
    'detail.rangeModeEnd': 'End anchor mode',
    'detail.rangeClear': 'Clear anchor',
    'detail.rangeAfter': 'Show from anchor',
    'detail.rangeAfterActive': 'Showing from anchor',
    'detail.rangeBefore': 'Show until anchor',
    'detail.rangeBeforeActive': 'Showing until anchor',
    'detail.bodyExpand': '▼ Show more',
    'detail.bodyCollapse': '▲ Show less',
    'calendar.clear': 'Clear',
    'calendar.today': 'Today',
    'session.labels.empty': 'No session labels yet',
    'session.labels.loading': 'Loading session labels...',
    'shortcut.title': 'Shortcuts',
    'shortcut.copy': 'Shortcuts do not run while an input is focused. Press Esc to close or leave search fields.',
    'shortcut.close': 'Close',
    'shortcut.refresh': 'Refresh the current list or session detail',
    'shortcut.toggleFilters': 'Toggle the left-pane filters',
    'shortcut.clearList': 'Run Clear on the left pane',
    'shortcut.focusSearch': 'Focus the search input',
    'shortcut.nextMatch': 'Move to the next detail-search match',
    'shortcut.prevMatch': 'Move to the previous detail-search match',
    'shortcut.meta': 'Toggle meta details for path / cwd / time',
    'shortcut.prevSession': 'Open the previous session',
    'shortcut.nextSession': 'Open the next session',
    'shortcut.onlyUser': 'Toggle only user instructions',
    'shortcut.onlyAi': 'Toggle only AI responses',
    'shortcut.turnBoundary': 'Toggle only each input and final reply',
    'shortcut.tokenUsage': 'Toggle only token usage',
    'shortcut.reverse': 'Toggle reverse order',
    'shortcut.clearDetail': 'Clear right-pane filters and active modes',
    'shortcut.toggleActions': 'Toggle detail actions',
    'shortcut.copyResume': 'Copy the session resume command',
    'shortcut.copyDisplayed': 'Copy displayed messages',
    'shortcut.toggleSelection': 'Toggle selection mode',
    'shortcut.copySelected': 'Copy selected messages',
    'shortcut.toggleRange': 'Toggle anchor mode',
    'shortcut.clearRange': 'Clear the anchor',
    'shortcut.before': 'Show only before the anchor',
    'shortcut.after': 'Show only after the anchor',
    'shortcut.escape': 'Close the shortcut list or label picker, and leave search fields.',
    'meta.sessionRoot': 'session root',
    'meta.path': 'path',
    'meta.cwd': 'cwd',
    'meta.time': 'time',
    'meta.usage': 'usage',
    'meta.models': 'models',
    'meta.effortLevel': 'Effort Level',
    'meta.fxRate': 'FX',
    'meta.status': 'status',
    'usage.model': 'model',
    'usage.input': 'input',
    'usage.cached': 'cache',
    'usage.cacheCreate': 'cache write',
    'usage.cacheRead': 'cache read',
    'usage.output': 'output',
    'usage.total': 'total',
    'usage.cost': 'cost',
    'usage.perDollar': 'per $1',
    'usage.score': 'score',
    'usage.rank': 'rank',
    'usage.tooltip.input': 'Number of input tokens sent to the model.',
    'usage.tooltip.cached': 'Combined cache write and cache read tokens.',
    'usage.tooltip.cacheCreate': 'Number of tokens used for cache write.',
    'usage.tooltip.cacheRead': 'Number of tokens used for cache read.',
    'usage.tooltip.output': 'Number of output tokens generated by the model.',
    'usage.tooltip.total': 'Total number of input, cache, and output tokens combined.',
    'usage.tooltip.cost': 'Recorded estimated USD cost for this usage.',
    'usage.tooltip.perDollar': 'Total tokens processed per estimated US dollar.',
    'usage.tooltip.score': 'Base-10 logarithm of total_tokens / cost_usd. A +1.0 increase means 10x better efficiency.',
    'usage.tooltip.rank': 'Fixed rank bucket derived from the score using SS / S / A / B / C / D / E thresholds.',
    'usage.costUnknown': 'cost unavailable',
    'usage.tokensUnit': 'tokens',
    'usage.totalTokens': 'total {count} tok',
    'usage.inputTokens': 'in {count}',
    'usage.outputTokens': 'out {count}',
    'usage.cacheCreateTokens': 'cache write {count}',
    'usage.cacheReadTokens': 'cache read {count}',
    'usage.costUsd': 'cost {cost}',
    'summary.labels': 'labels: sessions {sessions} / events {events}',
    'summary.sessions': 'sessions: {current} / {filtered} / {total}',
    'summary.events': 'events: {visible}/{total}',
    'summary.eventsLoading': 'events: loading...',
    'summary.raw': 'raw {count}',
    'session.preview.empty': '(no preview)',
    'status.sessions.loadingTitle': 'Loading sessions...',
    'status.sessions.loadingCopy': 'Checking the latest sessions.',
    'status.sessions.errorTitle': 'Failed to load the list',
    'status.sessions.noMatchesTitle': 'No sessions match these filters',
    'status.sessions.noMatchesCopy': 'Review the filters or run Reload.',
    'status.sessions.emptyTitle': 'No sessions found yet',
    'status.sessions.emptyCopy': 'Check whether the target directory contains .jsonl sessions.',
    'status.sessions.refreshTitle': 'Refreshing list...',
    'status.sessions.refreshCopy': 'Fetching the latest sessions again.',
    'status.sessions.backgroundTitle': 'Loading more sessions...',
    'status.sessions.backgroundCopy': 'The remaining sessions are being added in the background.',
    'status.sessions.backgroundInline': 'Loading more {loaded} / {total}',
    'status.labels.loadingTitle': 'Loading labeled items...',
    'status.labels.loadingCopy': 'Collecting labeled sessions and events.',
    'status.labels.errorTitle': 'Failed to load the label list',
    'status.labels.emptyTitle': 'No labeled items yet',
    'status.labels.emptyCopy': 'Labeled sessions and events will appear here.',
    'status.detail.loadingTitle': 'Loading session detail...',
    'status.detail.loadingCopy': 'Fetching events.',
    'status.detail.errorTitle': 'Failed to load detail',
    'status.detail.selectSession': 'Select a session',
    'status.detail.noDisplayTitle': 'No events to display',
    'status.detail.noDisplayCopy': 'This session has no displayable events.',
    'status.detail.noMatchTitle': 'No events match these conditions',
    'status.detail.noMatchCopy': 'Changing the display conditions may reveal events.',
    'status.detail.refreshTitle': 'Refreshing session detail...',
    'status.detail.refreshCopy': 'Fetching the latest events again.',
    'error.sessions': 'Failed to load the session list',
    'error.detail': 'Failed to load the session detail',
    'picker.noLabels': 'No labels exist yet. Create one in Label Manager first.',
    'picker.removeLabel': 'Remove label',
    'picker.addLabel': 'Add label',
    'copy.copied': 'Copied',
    'copy.displayedCount': 'Copied {count}',
    'copy.selectedCount': 'Copied {count}',
    'copy.single': 'Copy',
  },
  'zh-Hans': {
    'language.selector': '语言',
    'currency.selector': '货币',
    'header.subtitle': '可以通过列表和详细视图查看 ClaudeCode 的事件历史，并进行搜索。\n还可以给想保留的内容加上标签，之后再轻松找到。',
    'header.shortcuts': '快捷键',
    'header.meta.show': '显示元信息',
    'header.meta.hide': '隐藏元信息',
    'header.list.hide': '隐藏会话列表',
    'header.list.show': '显示会话列表',
    'header.list.hideShort': '隐藏列表',
    'header.list.showShort': '显示列表',
    'header.labels': '标签管理',
    'header.costs': '成本汇总',
    'leftPane.tab.sessions': '会话列表',
    'leftPane.tab.labels': '标签列表',
    'labelsPane.kicker': 'Label Browser',
    'labelsPane.heading': '标签列表',
    'labelsPane.copy': '这里会集中列出带标签的会话和事件。',
    'labelsPane.group.sessions': '带标签的会话',
    'labelsPane.group.events': '带标签的事件',
    'labelsPane.item.session': 'session',
    'labelsPane.item.event': 'event',
    'labelsPane.kind.message': 'message',
    'labelsPane.kind.function_call': 'function call',
    'labelsPane.kind.function_output': 'function output',
    'labelsPane.kind.agent_update': 'agent update',
    'labelsPane.kind.token_usage': 'token usage',
    'todayUsage.title': '今天',
    'todayUsage.loading': '正在加载今天的 usage...',
    'todayUsage.empty': '今天还没有 usage',
    'todayUsage.error': '无法获取今天的 usage',
    'todaySummary.label': '今天',
    'todaySummary.loading': '正在加载今天的 usage...',
    'todaySummary.empty': '今天还没有 token usage。',
    'todaySummary.error': '获取今天的 usage 失败。',
    'todaySummary.refreshing': '更新中...',
    'todaySummary.scope.tokenEvent': 'token usage',
    'todaySummary.scope.session': 'session',
    'toolbar.kicker': 'Session Browser',
    'toolbar.heading': '搜索与筛选',
    'toolbar.copy': '筛选条件会在下次启动时继续保留。',
    'toolbar.reload': 'Reload',
    'toolbar.clear': 'Clear',
    'toolbar.filters.hide': '隐藏筛选',
    'toolbar.filters.show': '显示筛选',
    'search.title': '搜索',
    'search.copy': '先用 cwd 和关键词缩小候选范围。',
    'search.cwd': '工作目录',
    'search.keyword': '关键词',
    'search.mode': '模式',
    'filter.title': '筛选',
    'filter.copy': '按时间范围、source 和标签整理列表。',
    'filter.dateFrom': '开始日期',
    'filter.dateTo': '结束日期',
    'filter.eventDateFrom': '事件开始日期/时间',
    'filter.eventDateTo': '事件结束日期/时间',
    'common.date': '日期',
    'common.time': '时间',
    'filter.source': '来源',
    'filter.subagents': 'subagents',
    'filter.sessionLabel': '会话标签',
    'filter.eventLabel': '事件标签',
    'filter.source.all': 'source: all',
    'filter.source.cli': 'source: Claude Code CLI',
    'filter.source.vscode': 'source: Claude Desktop',
    'filter.subagents.include': '包含',
    'filter.subagents.exclude': '不包含',
    'filter.sessionLabel.all': 'session label: all',
    'filter.eventLabel.all': 'event label: all',
    'filter.sort': '排序',
    'filter.sort.desc': '最新优先',
    'filter.sort.asc': '最旧优先',
    'filter.sort.updated': '最后更新时间',
    'filter.mode.and': 'keyword AND',
    'filter.mode.or': 'keyword OR',
    'placeholder.cwd': 'cwd（部分匹配）',
    'placeholder.keyword': '关键词筛选',
    'placeholder.detailKeyword': '详细关键词',
    'detail.display': '显示',
    'detail.toggle.user': '仅显示用户指令',
    'detail.toggle.ai': '仅显示 AI 回复',
    'detail.toggle.turn': '仅显示每次输入与最终回复',
    'detail.toggle.tokenUsage': '仅显示 token usage',
    'detail.toggle.reverse': '反转显示顺序',
    'detail.toggle.selectedOnly': '仅显示已选事件',
    'detail.label': '标签筛选',
    'detail.label.all': 'all',
    'detail.costSort': '成本排序',
    'detail.costSort.total': '总 token 排序',
    'detail.costSort.cost': '成本排序',
    'detail.costSort.score': 'score 排序',
    'detail.refresh': 'Refresh',
    'detail.refreshing': 'Refreshing...',
    'detail.clear': 'Clear',
    'detail.actions.hide': '隐藏详细操作',
    'detail.actions.show': '显示详细操作',
    'detail.actions': '操作',
    'detail.copyResume': '复制恢复命令',
    'detail.addSessionLabel': '为会话添加标签',
    'detail.copyDisplayed': '复制当前显示消息',
    'detail.selectMode': '选择模式',
    'detail.selectEnd': '结束选择',
    'detail.copySelected': '复制已选',
    'detail.copySelectedCount': '复制已选（{count}）',
    'detail.search': '搜索',
    'detail.searchKeyword': '详细关键词',
    'detail.searchFilter': '筛选',
    'detail.searchFilterClear': '清除过滤',
    'detail.searchRun': '搜索',
    'detail.prev': '上一项',
    'detail.next': '下一项',
    'detail.matchCounter': '{current} / {total}',
    'detail.searchClear': '清除搜索',
    'detail.eventDateFrom': '事件开始日期/时间',
    'detail.eventDateTo': '事件结束日期/时间',
    'detail.eventDateClear': '清除日期',
    'detail.range': '范围',
    'detail.rangeMode': '锚点模式',
    'detail.rangeModeEnd': '结束锚点模式',
    'detail.rangeClear': '清除锚点',
    'detail.rangeAfter': '仅显示锚点之后',
    'detail.rangeAfterActive': '正在显示锚点之后',
    'detail.rangeBefore': '仅显示锚点之前',
    'detail.rangeBeforeActive': '正在显示锚点之前',
    'detail.bodyExpand': '▼ 展开更多',
    'detail.bodyCollapse': '▲ 收起',
    'calendar.clear': '清除',
    'calendar.today': '今天',
    'session.labels.empty': '还没有会话标签',
    'session.labels.loading': '正在加载会话标签...',
    'shortcut.title': '快捷键',
    'shortcut.copy': '输入框获得焦点时不会触发快捷键。按 Esc 可关闭，或离开搜索输入框。',
    'shortcut.close': '关闭',
    'shortcut.refresh': '刷新当前列表或会话详情',
    'shortcut.toggleFilters': '切换左侧筛选显示',
    'shortcut.clearList': '执行左侧 Clear',
    'shortcut.focusSearch': '聚焦到搜索输入框',
    'shortcut.nextMatch': '跳到详细搜索的下一个命中',
    'shortcut.prevMatch': '跳到详细搜索的上一个命中',
    'shortcut.meta': '切换 path / cwd / time 元信息显示',
    'shortcut.prevSession': '打开上一个会话',
    'shortcut.nextSession': '打开下一个会话',
    'shortcut.onlyUser': '切换仅显示用户指令',
    'shortcut.onlyAi': '切换仅显示 AI 回复',
    'shortcut.turnBoundary': '切换仅显示每次输入与最终回复',
    'shortcut.tokenUsage': '切换仅显示 token usage',
    'shortcut.reverse': '切换反转显示顺序',
    'shortcut.clearDetail': '清除右侧筛选与当前模式',
    'shortcut.toggleActions': '切换详细操作显示',
    'shortcut.copyResume': '复制会话恢复命令',
    'shortcut.copyDisplayed': '复制当前显示消息',
    'shortcut.toggleSelection': '切换选择模式',
    'shortcut.copySelected': '复制已选消息',
    'shortcut.toggleRange': '切换锚点模式',
    'shortcut.clearRange': '清除锚点',
    'shortcut.before': '仅显示锚点之前',
    'shortcut.after': '仅显示锚点之后',
    'shortcut.escape': '关闭快捷键列表或标签选择框，并离开搜索输入框。',
    'meta.sessionRoot': 'session root',
    'meta.path': 'path',
    'meta.cwd': 'cwd',
    'meta.time': 'time',
    'meta.usage': 'usage',
    'meta.models': '模型',
    'meta.effortLevel': 'Effort Level',
    'meta.fxRate': '汇率',
    'meta.status': 'status',
    'usage.model': '模型',
    'usage.input': '输入',
    'usage.cached': '缓存',
    'usage.cacheCreate': '缓存写入',
    'usage.cacheRead': '缓存读取',
    'usage.output': '输出',
    'usage.total': '总计',
    'usage.cost': '成本',
    'usage.perDollar': '每 $1',
    'usage.score': '评分',
    'usage.rank': '等级',
    'usage.tooltip.input': '发送给模型的输入 token 数。',
    'usage.tooltip.cached': 'cache 写入与 cache 读取合计的 token 数。',
    'usage.tooltip.cacheCreate': '用于 cache 写入的 token 数。',
    'usage.tooltip.cacheRead': '用于 cache 读取的 token 数。',
    'usage.tooltip.output': '模型生成的输出 token 数。',
    'usage.tooltip.total': 'input + cache + output 的总 token 数。',
    'usage.tooltip.cost': '这次 usage 记录的预估成本（USD）。',
    'usage.tooltip.perDollar': '按预估成本换算，每 1 美元对应的总 token 数。',
    'usage.tooltip.score': '总 token 数 / 成本(USD) 的常用对数。每增加 1.0，成本效益提升 10 倍。',
    'usage.tooltip.rank': '根据 score 的固定阈值划分为 SS / S / A / B / C / D / E 等级。',
    'usage.costUnknown': '成本未知',
    'usage.tokensUnit': 'tokens',
    'usage.totalTokens': '总计 {count} tok',
    'usage.inputTokens': '输入 {count}',
    'usage.outputTokens': '输出 {count}',
    'usage.cacheCreateTokens': '缓存写入 {count}',
    'usage.cacheReadTokens': '缓存读取 {count}',
    'usage.costUsd': '费用 {cost}',
    'summary.labels': 'labels: sessions {sessions} / events {events}',
    'summary.sessions': 'sessions: {current} / {filtered} / {total}',
    'summary.events': 'events: {visible}/{total}',
    'summary.eventsLoading': 'events: loading...',
    'summary.raw': 'raw {count}',
    'session.preview.empty': '(无预览)',
    'status.sessions.loadingTitle': '正在加载会话列表...',
    'status.sessions.loadingCopy': '正在检查最新会话。',
    'status.sessions.errorTitle': '加载列表失败',
    'status.sessions.noMatchesTitle': '没有符合筛选条件的会话',
    'status.sessions.noMatchesCopy': '请检查筛选条件或执行 Reload。',
    'status.sessions.emptyTitle': '尚未找到会话',
    'status.sessions.emptyCopy': '请确认目标目录中是否存在 .jsonl 会话文件。',
    'status.sessions.refreshTitle': '正在刷新列表...',
    'status.sessions.refreshCopy': '正在重新获取最新会话。',
    'status.sessions.backgroundTitle': '正在继续加载其余会话...',
    'status.sessions.backgroundCopy': '不会打断当前操作，列表会逐步追加。',
    'status.sessions.backgroundInline': '继续加载 {loaded} / {total}',
    'status.labels.loadingTitle': '正在加载标签列表...',
    'status.labels.loadingCopy': '正在整理带标签的会话和事件。',
    'status.labels.errorTitle': '获取标签列表失败',
    'status.labels.emptyTitle': '还没有带标签的项目',
    'status.labels.emptyCopy': '给会话或事件添加标签后，会显示在这里。',
    'status.detail.loadingTitle': '正在加载会话详情...',
    'status.detail.loadingCopy': '正在获取事件。',
    'status.detail.errorTitle': '加载详情失败',
    'status.detail.selectSession': '请选择一个会话',
    'status.detail.noDisplayTitle': '没有可显示的事件',
    'status.detail.noDisplayCopy': '此会话中没有可显示的事件。',
    'status.detail.noMatchTitle': '没有符合条件的事件',
    'status.detail.noMatchCopy': '调整显示条件后可能会出现事件。',
    'status.detail.refreshTitle': '正在刷新会话详情...',
    'status.detail.refreshCopy': '正在重新获取最新事件。',
    'error.sessions': '获取会话列表失败',
    'error.detail': '获取会话详情失败',
    'picker.noLabels': '还没有标签。请先在标签管理中创建标签。',
    'picker.removeLabel': '移除标签',
    'picker.addLabel': '添加标签',
    'copy.copied': '已复制',
    'copy.displayedCount': '已复制 {count} 项',
    'copy.selectedCount': '已复制 {count} 项',
    'copy.single': '复制',
  },
};
I18N['zh-Hant'] = {
  ...I18N['zh-Hans'],
  'language.selector': '語言',
  'currency.selector': '幣別',
  'header.subtitle': '可以透過列表與詳細檢視查看 ClaudeCode 的事件歷史，並進行搜尋。\n還可以替想保留的內容加上標籤，之後再輕鬆找到。',
  'header.meta.show': '顯示中繼資訊',
  'header.meta.hide': '隱藏中繼資訊',
  'header.list.hide': '隱藏工作階段列表',
  'header.list.show': '顯示工作階段列表',
  'header.list.hideShort': '隱藏列表',
  'header.list.showShort': '顯示列表',
  'header.labels': '標籤管理',
  'header.costs': '成本彙總',
  'todayUsage.title': '今天',
  'todayUsage.loading': '正在載入今天的 usage...',
  'todayUsage.empty': '今天還沒有 usage',
  'todayUsage.error': '無法取得今天的 usage',
  'todaySummary.label': '今天',
  'todaySummary.loading': '正在載入今天的 usage...',
  'todaySummary.empty': '今天還沒有 token usage。',
  'todaySummary.error': '取得今天的 usage 失敗。',
  'todaySummary.refreshing': '更新中...',
  'todaySummary.scope.tokenEvent': 'token usage',
  'todaySummary.scope.session': 'session',
  'toolbar.heading': '搜尋與篩選',
  'toolbar.copy': '篩選條件會在下次啟動時繼續保留。',
  'toolbar.filters.hide': '隱藏篩選',
  'toolbar.filters.show': '顯示篩選',
  'search.title': '搜尋',
  'search.copy': '先用 cwd 和關鍵字縮小候選範圍。',
  'search.cwd': '工作目錄',
  'search.keyword': '關鍵字',
  'filter.sessionLabel': '工作階段標籤',
  'filter.title': '篩選',
  'filter.copy': '按時間範圍、source 和標籤整理列表。',
  'filter.dateFrom': '開始日期',
  'filter.dateTo': '結束日期',
  'filter.eventDateFrom': '事件開始日期/時間',
  'filter.eventDateTo': '事件結束日期/時間',
  'common.date': '日期',
  'common.time': '時間',
  'filter.source': '來源',
  'filter.subagents': 'subagents',
  'filter.eventLabel': '事件標籤',
  'filter.subagents.include': '包含',
  'filter.subagents.exclude': '不包含',
  'filter.sort': '排序',
  'filter.sort.desc': '最新優先',
  'filter.sort.asc': '最舊優先',
  'filter.sort.updated': '最後更新時間',
  'placeholder.cwd': 'cwd（部分比對）',
  'placeholder.keyword': '關鍵字篩選',
  'placeholder.detailKeyword': '詳細關鍵字',
  'detail.display': '顯示',
  'detail.toggle.user': '僅顯示使用者指示',
  'detail.toggle.ai': '僅顯示 AI 回覆',
  'detail.toggle.turn': '僅顯示每次輸入與最終回覆',
  'detail.toggle.tokenUsage': '僅顯示 token usage',
  'detail.toggle.reverse': '反轉顯示順序',
  'detail.toggle.selectedOnly': '僅顯示已選事件',
  'detail.label': '標籤篩選',
  'detail.label.all': 'all',
  'detail.costSort': '成本排序',
  'detail.costSort.total': '總 token 排序',
  'detail.costSort.cost': '成本排序',
  'detail.costSort.score': 'score 排序',
  'detail.refresh': '刷新',
  'detail.refreshing': '正在刷新...',
  'detail.clear': '清除',
  'detail.actions.hide': '隱藏詳細操作',
  'detail.actions.show': '顯示詳細操作',
  'detail.actions': '操作',
  'detail.copyResume': '複製恢復命令',
  'detail.addSessionLabel': '為工作階段新增標籤',
  'detail.copyDisplayed': '複製目前顯示訊息',
  'detail.selectMode': '選取模式',
  'detail.selectEnd': '結束選取',
  'detail.copySelected': '複製已選',
  'detail.copySelectedCount': '複製已選（{count}）',
  'detail.search': '搜尋',
  'detail.searchKeyword': '詳細關鍵字',
  'detail.searchFilterClear': '清除篩選',
  'detail.searchRun': '搜尋',
  'detail.searchFilter': '篩選',
  'detail.prev': '上一項',
  'detail.next': '下一項',
  'detail.matchCounter': '{current} / {total}',
  'detail.searchClear': '清除搜尋',
  'detail.eventDateFrom': '事件開始日期/時間',
  'detail.eventDateTo': '事件結束日期/時間',
  'detail.eventDateClear': '清除日期',
  'detail.range': '範圍',
  'detail.rangeMode': '錨點模式',
  'detail.rangeModeEnd': '結束錨點模式',
  'detail.rangeClear': '清除錨點',
  'detail.rangeAfter': '僅顯示錨點之後',
  'detail.rangeAfterActive': '正在顯示錨點之後',
  'detail.rangeBefore': '僅顯示錨點之前',
  'detail.rangeBeforeActive': '正在顯示錨點之前',
  'detail.bodyExpand': '▼ 展開更多',
  'detail.bodyCollapse': '▲ 收起',
  'session.labels.empty': '尚未有工作階段標籤',
  'session.labels.loading': '正在載入工作階段標籤...',
  'shortcut.title': '快捷鍵',
  'shortcut.copy': '輸入框取得焦點時不會觸發快捷鍵。按 Esc 可關閉，或離開搜尋輸入框。',
  'shortcut.close': '關閉',
  'shortcut.refresh': '重新整理目前列表或工作階段詳情',
  'shortcut.toggleFilters': '切換左側篩選顯示',
  'shortcut.clearList': '執行左側 Clear',
  'shortcut.focusSearch': '將焦點移到搜尋輸入框',
  'shortcut.nextMatch': '跳到詳細搜尋的下一個命中',
  'shortcut.prevMatch': '跳到詳細搜尋的上一個命中',
  'shortcut.meta': '切換 path / cwd / time 中繼資訊顯示',
  'shortcut.prevSession': '開啟上一個工作階段',
  'shortcut.nextSession': '開啟下一個工作階段',
  'shortcut.onlyUser': '切換僅顯示使用者指示',
  'shortcut.onlyAi': '切換僅顯示 AI 回覆',
  'shortcut.turnBoundary': '切換僅顯示每次輸入與最終回覆',
  'shortcut.tokenUsage': '切換僅顯示 token usage',
  'shortcut.reverse': '切換反轉顯示順序',
  'shortcut.clearDetail': '清除右側篩選與目前模式',
  'shortcut.toggleActions': '切換詳細操作顯示',
  'shortcut.copyResume': '複製工作階段恢復命令',
  'shortcut.copyDisplayed': '複製目前顯示訊息',
  'shortcut.toggleSelection': '切換選取模式',
  'shortcut.copySelected': '複製已選訊息',
  'shortcut.toggleRange': '切換錨點模式',
  'shortcut.clearRange': '清除錨點',
  'shortcut.before': '僅顯示錨點之前',
  'shortcut.after': '僅顯示錨點之後',
  'meta.models': '模型',
  'meta.usage': 'usage',
  'meta.effortLevel': 'Effort Level',
  'meta.fxRate': '匯率',
  'usage.model': '模型',
  'usage.input': '輸入',
  'usage.cached': '快取',
  'usage.cacheCreate': '快取寫入',
  'usage.cacheRead': '快取讀取',
  'usage.output': '輸出',
  'usage.total': '總計',
  'usage.cost': '成本',
  'usage.perDollar': '每 $1',
  'usage.score': '評分',
  'usage.rank': '等級',
  'usage.tooltip.input': '傳送給模型的輸入 token 數。',
  'usage.tooltip.cached': 'cache 寫入與 cache 讀取合計的 token 數。',
  'usage.tooltip.cacheCreate': '用於 cache 寫入的 token 數。',
  'usage.tooltip.cacheRead': '用於 cache 讀取的 token 數。',
  'usage.tooltip.output': '模型產生的輸出 token 數。',
  'usage.tooltip.total': 'input + cache + output 的總 token 數。',
  'usage.tooltip.cost': '這次 usage 記錄的預估成本（USD）。',
  'usage.tooltip.perDollar': '按預估成本換算，每 1 美元對應的總 token 數。',
  'usage.tooltip.score': '總 token 數 / 成本(USD) 的常用對數。每增加 1.0，成本效益提升 10 倍。',
  'usage.tooltip.rank': '根據 score 的固定門檻劃分為 SS / S / A / B / C / D / E 等級。',
  'usage.costUnknown': '成本未知',
  'usage.tokensUnit': 'tokens',
  'usage.totalTokens': '總計 {count} tok',
  'usage.inputTokens': '輸入 {count}',
  'usage.outputTokens': '輸出 {count}',
  'usage.cacheCreateTokens': '快取寫入 {count}',
  'usage.cacheReadTokens': '快取讀取 {count}',
  'usage.costUsd': '費用 {cost}',
  'shortcut.escape': '關閉快捷鍵列表或標籤選擇框，並離開搜尋輸入框。',
  'summary.sessions': 'sessions: {filtered}/{total}',
  'session.preview.empty': '(無預覽)',
  'status.sessions.loadingTitle': '正在載入工作階段列表...',
  'status.sessions.loadingCopy': '正在檢查最新工作階段。',
  'status.sessions.errorTitle': '載入列表失敗',
  'status.sessions.noMatchesTitle': '沒有符合篩選條件的工作階段',
  'status.sessions.noMatchesCopy': '請檢查篩選條件或執行 Reload。',
  'status.sessions.emptyTitle': '尚未找到工作階段',
  'status.sessions.emptyCopy': '請確認目標目錄中是否存在 .jsonl 工作階段檔案。',
  'status.sessions.refreshTitle': '正在刷新列表...',
  'status.sessions.refreshCopy': '正在重新取得最新工作階段。',
  'status.sessions.backgroundTitle': '正在繼續載入其餘工作階段...',
  'status.sessions.backgroundCopy': '不會打斷目前操作，列表會逐步追加。',
  'status.sessions.backgroundInline': '繼續載入 {loaded} / {total}',
  'status.detail.loadingTitle': '正在載入工作階段詳情...',
  'status.detail.loadingCopy': '正在取得事件。',
  'status.detail.errorTitle': '載入詳情失敗',
  'status.detail.selectSession': '請選擇一個工作階段',
  'status.detail.noDisplayTitle': '沒有可顯示的事件',
  'status.detail.noDisplayCopy': '此工作階段中沒有可顯示的事件。',
  'status.detail.noMatchTitle': '沒有符合條件的事件',
  'status.detail.noMatchCopy': '調整顯示條件後可能會出現事件。',
  'status.detail.refreshTitle': '正在刷新工作階段詳情...',
  'status.detail.refreshCopy': '正在重新取得最新事件。',
  'error.sessions': '取得工作階段列表失敗',
  'error.detail': '取得工作階段詳情失敗',
  'picker.noLabels': '尚未有標籤。請先在標籤管理中建立標籤。',
  'picker.removeLabel': '移除標籤',
  'picker.addLabel': '新增標籤',
  'copy.copied': '已複製',
  'copy.displayedCount': '已複製 {count} 項',
  'copy.selectedCount': '已複製 {count} 項',
  'copy.single': '複製',
};
let uiLanguage = 'ja';
let selectedCostCurrency = 'USD';

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

function t(key, vars){
  const dict = I18N[uiLanguage] || I18N.ja;
  let text = dict[key];
  if(typeof text !== 'string'){
    text = I18N.ja[key] || key;
  }
  if(vars){
    Object.entries(vars).forEach(([name, value]) => {
      text = text.replaceAll(`{${name}}`, String(value));
    });
  }
  return text;
}

function setText(selector, value){
  const element = document.querySelector(selector);
  if(element){
    element.textContent = value;
  }
}

function setTextById(id, value){
  const element = document.getElementById(id);
  if(element){
    element.textContent = value;
  }
}

function setFieldLabel(inputId, value){
  const input = document.getElementById(inputId);
  const label = input ? input.closest('label') : null;
  const span = label ? label.querySelector('span') : null;
  if(span){
    span.textContent = value;
  }
}

function setInputAriaLabel(id, value){
  const input = document.getElementById(id);
  if(input){
    input.setAttribute('aria-label', value);
  }
  const seg = segInstances[id];
  if(seg && seg.wrap){
    seg.wrap.setAttribute('aria-label', value);
  }
}

function setDateTimePairAria(dateId, timeId, label){
  setInputAriaLabel(dateId, label);
}

function setToggleLabel(inputId, value){
  const input = document.getElementById(inputId);
  const label = input ? input.closest('label') : null;
  if(!label){
    return;
  }
  const textNode = Array.from(label.childNodes).find(node => node.nodeType === Node.TEXT_NODE);
  if(textNode){
    textNode.textContent = ` ${value}`;
  }
}

function setOptionText(selectId, index, value){
  const select = document.getElementById(selectId);
  if(select && select.options[index]){
    select.options[index].textContent = value;
  }
}

function applyMainLanguage(){
  document.documentElement.lang = uiLanguage;
  document.title = 'Claude Sessions Viewer';
  document.getElementById('language_select').value = uiLanguage;
  document.getElementById('language_select').setAttribute('aria-label', t('language.selector'));
  const currencySelect = document.getElementById('currency_select');
  if(currencySelect){
    currencySelect.value = getPreferredCostCurrencyCode();
    currencySelect.setAttribute('aria-label', t('currency.selector'));
  }
  setText('.header-subtitle', t('header.subtitle'));
  setTextById('open_shortcuts', t('header.shortcuts'));
  document.getElementById('open_shortcuts').setAttribute('title', t('header.shortcuts'));
  setTextById('open_label_manager', t('header.labels'));
  setTextById('open_cost_summary', t('header.costs'));
  setTextById('left_tab_sessions', t('leftPane.tab.sessions'));
  setTextById('left_tab_labels', t('leftPane.tab.labels'));
  setTextById('label_browser_kicker', t('labelsPane.kicker'));
  setTextById('label_browser_heading', t('labelsPane.heading'));
  setTextById('label_browser_copy', t('labelsPane.copy'));
  setText('.toolbar .section-kicker', t('toolbar.kicker'));
  setText('.toolbar .toolbar-heading', t('toolbar.heading'));
  setText('.toolbar .toolbar-copy', t('toolbar.copy'));
  setTextById('reload', t('toolbar.reload'));
  setTextById('clear', t('toolbar.clear'));
  setText('.toolbar-section:nth-of-type(1) .toolbar-section-title', t('search.title'));
  setText('.toolbar-section:nth-of-type(1) .toolbar-section-copy', t('search.copy'));
  setFieldLabel('cwd_q', t('search.cwd'));
  setFieldLabel('q', t('search.keyword'));
  setFieldLabel('mode', t('search.mode'));
  setText('.toolbar-section:nth-of-type(2) .toolbar-section-title', t('filter.title'));
  setText('.toolbar-section:nth-of-type(2) .toolbar-section-copy', t('filter.copy'));
  setFieldLabel('date_from', t('filter.dateFrom'));
  setFieldLabel('date_to', t('filter.dateTo'));
  setTextById('event_date_from_label', t('filter.eventDateFrom'));
  setTextById('event_date_to_label', t('filter.eventDateTo'));
  setInputAriaLabel('date_from', t('filter.dateFrom'));
  setInputAriaLabel('date_to', t('filter.dateTo'));
  setDateTimePairAria('event_date_from_date', 'event_date_from_time', t('filter.eventDateFrom'));
  setDateTimePairAria('event_date_to_date', 'event_date_to_time', t('filter.eventDateTo'));
  setFieldLabel('source_filter', t('filter.source'));
  setFieldLabel('subagents_filter', t('filter.subagents'));
  setFieldLabel('session_label_filter', t('filter.sessionLabel'));
  setFieldLabel('event_label_filter', t('filter.eventLabel'));
  document.querySelectorAll('.sort-tab').forEach(tab => {
    const key = 'filter.sort.' + tab.dataset.sort;
    tab.textContent = t(key);
  });
  document.getElementById('cwd_q').placeholder = t('placeholder.cwd');
  document.getElementById('q').placeholder = t('placeholder.keyword');
  document.getElementById('detail_keyword_q').placeholder = t('placeholder.detailKeyword');
  setOptionText('mode', 0, t('filter.mode.and'));
  setOptionText('mode', 1, t('filter.mode.or'));
  setOptionText('source_filter', 0, t('filter.source.all'));
  setOptionText('source_filter', 1, t('filter.source.cli'));
  setOptionText('source_filter', 2, t('filter.source.vscode'));
  setOptionText('subagents_filter', 0, t('filter.subagents.include'));
  setOptionText('subagents_filter', 1, t('filter.subagents.exclude'));
  setText('.detail-toolbar-row.primary .detail-group-title', t('detail.display'));
  setToggleLabel('only_user_instruction', t('detail.toggle.user'));
  setToggleLabel('only_ai_response', t('detail.toggle.ai'));
  setToggleLabel('turn_boundary_only', t('detail.toggle.turn'));
  document.getElementById('turn_boundary_only').closest('label').setAttribute('title', '3');
  setToggleLabel('only_token_usage', t('detail.toggle.tokenUsage'));
  setToggleLabel('reverse_order', t('detail.toggle.reverse'));
  setToggleLabel('selected_events_only', t('detail.toggle.selectedOnly'));
  setFieldLabel('detail_cost_sort', t('detail.costSort'));
  document.getElementById('detail_cost_sort').setAttribute('title', t('detail.costSort'));
  setOptionText('detail_cost_sort', 0, '');
  setOptionText('detail_cost_sort', 1, t('detail.costSort.total'));
  setOptionText('detail_cost_sort', 2, t('detail.costSort.cost'));
  setOptionText('detail_cost_sort', 3, t('detail.costSort.score'));
  setFieldLabel('detail_event_label_filter', t('detail.label'));
  document.getElementById('detail_event_label_filter').setAttribute('title', t('detail.label'));
  setTextById('clear_detail', t('detail.clear'));
  setTextById('toggle_detail_actions', t('detail.actions'));
  setTextById('toggle_detail_search', t('detail.search'));
  setTextById('toggle_detail_range', t('detail.range'));
  setText('.detail-toolbar-row.secondary .detail-group-title', t('detail.actions'));
  setTextById('copy_resume_command', t('detail.copyResume'));
  setTextById('add_session_label', t('detail.addSessionLabel'));
  setTextById('copy_displayed_messages', t('detail.copyDisplayed'));
  setText('.detail-toolbar-row.keyword .detail-group-title', t('detail.search'));
  setFieldLabel('detail_keyword_q', t('detail.searchKeyword'));
  document.getElementById('detail_keyword_q').setAttribute('title', '/');
  setTextById('detail_keyword_filter', t('detail.searchFilter'));
  setTextById('detail_keyword_search', t('detail.searchRun'));
  setTextById('detail_keyword_prev', t('detail.prev'));
  setTextById('detail_keyword_next', t('detail.next'));
  setTextById('detail_keyword_clear', t('detail.searchClear'));
  setTextById('detail_event_date_from_label', t('detail.eventDateFrom'));
  setTextById('detail_event_date_to_label', t('detail.eventDateTo'));
  setDateTimePairAria('detail_event_date_from_date', 'detail_event_date_from_time', t('detail.eventDateFrom'));
  setDateTimePairAria('detail_event_date_to_date', 'detail_event_date_to_time', t('detail.eventDateTo'));
  setTextById('clear_detail_event_date', t('detail.eventDateClear'));
  setText('.detail-toolbar-row.range .detail-group-title', t('detail.range'));
  setTextById('clear_message_range_selection', t('detail.rangeClear'));
  const shortcutDescriptions = [
    'shortcut.refresh',
    'shortcut.toggleFilters',
    'shortcut.clearList',
    'shortcut.focusSearch',
    'shortcut.nextMatch',
    'shortcut.prevMatch',
    'shortcut.meta',
    'shortcut.prevSession',
    'shortcut.nextSession',
    'shortcut.onlyUser',
    'shortcut.onlyAi',
    'shortcut.turnBoundary',
    'shortcut.reverse',
    'shortcut.tokenUsage',
    'shortcut.clearDetail',
    'shortcut.toggleActions',
    'shortcut.copyResume',
    'shortcut.copyDisplayed',
    'shortcut.toggleSelection',
    'shortcut.copySelected',
    'shortcut.toggleRange',
    'shortcut.clearRange',
    'shortcut.before',
    'shortcut.after',
    'shortcut.escape',
  ];
  document.querySelectorAll('.shortcut-desc').forEach((element, index) => {
    const key = shortcutDescriptions[index];
    if(key){
      element.textContent = t(key);
    }
  });
  setTextById('shortcut_dialog_title', t('shortcut.title'));
  setText('.shortcut-copy', t('shortcut.copy'));
  setTextById('close_shortcuts', t('shortcut.close'));
  populateLabelControls();
  refreshDateTimeInputPairStates();
  updateFilterVisibility();
  updateDetailActionsVisibility();
  updateDetailMetaVisibility();
  updateLeftPaneVisibility();
  updateRefreshDetailButtonState();
  updateDetailDisplayControlsState();
  updateClearDetailButtonState();
  updateCopyResumeButtonState();
  updateDisplayedMessagesCopyButtonState();
  updateEventSelectionModeButtonState();
  updateCopySelectedMessagesButtonState();
  updateMessageRangeSelectionModeButtonState();
  updateClearMessageRangeSelectionButtonState();
  updateMessageRangeFilterButtonsState();
  renderLeftPaneTabs();
  renderSessionList();
  renderLabeledList();
  renderSessionLabelStrip();
  renderActiveSession();
  renderTodayUsageSummary();
  initAllFlatpickr();
}

function setUiLanguage(nextLanguage, persist){
  const normalized = normalizeLanguage(nextLanguage);
  uiLanguage = normalized;
  if(persist !== false){
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, normalized);
    } catch (e) {
      // Ignore storage write errors.
    }
  }
  applyMainLanguage();
}

function setCostCurrency(nextCurrency, persist){
  const normalized = normalizeCostCurrency(nextCurrency) || getDefaultCostCurrencyForLanguage(uiLanguage);
  selectedCostCurrency = normalized;
  if(persist !== false){
    try {
      localStorage.setItem(COST_CURRENCY_STORAGE_KEY, normalized);
    } catch (e) {
      // Ignore storage write errors.
    }
  }
  applyMainLanguage();
}

function readStoredLanguage(){
  try {
    return localStorage.getItem(LANGUAGE_STORAGE_KEY) || '';
  } catch (e) {
    return '';
  }
}

function readStoredCostCurrency(){
  try {
    return localStorage.getItem(COST_CURRENCY_STORAGE_KEY) || '';
  } catch (e) {
    return '';
  }
}

function getRequestedLanguage(){
  const params = new URLSearchParams(window.location.search);
  return normalizeLanguage(params.get('lang') || readStoredLanguage() || uiLanguage);
}

function getRequestedCostCurrency(){
  const params = new URLSearchParams(window.location.search);
  const requested = normalizeCostCurrency(params.get('currency') || readStoredCostCurrency() || '');
  return requested || getDefaultCostCurrencyForLanguage(uiLanguage);
}

const SEARCH_DEBOUNCE_MS = 180;
const BUTTON_FEEDBACK_MS = 1200;
const DETAIL_INTERACTION_LOCK_MS = 4000;
const API_REQUEST_TIMEOUT_MS = 30000;
let loadSessionsTimer = null;
let loadSessionsRequestSeq = 0;
let loadSessionDetailRequestSeq = 0;
let loadLabeledItemsRequestSeq = 0;
let todayUsageSummary = null;
let todayUsageExchangeRate = null;
let todayUsageSummaryStatus = 'idle';
let todayUsageSummaryMessage = '';
let todayUsageSummaryRequestSeq = 0;
let todayUsageSummaryTimer = 0;
let costSummaryRefreshTimer = 0;
let saveFiltersFrame = 0;
let deferredDetailSyncTimer = 0;
let labelManagerWindow = null;
let labelPickerHandler = null;
let datePickers = [];
let dateTimePickers = [];
let filtersVisible = false;
let detailPanelSection = '';
let detailMetaVisible = false;
let leftPaneVisible = true;
let pendingAutomaticDetailSync = false;
let detailPointerDown = false;
let detailInteractionLockUntil = 0;
let compactUiActive = false;
let compactUiSnapshot = null;
const detailExpandedEventKeysByPath = new Map();
let detailKeywordFilterTerm = '';
let detailKeywordSearchTerm = '';
let detailKeywordCurrentMatchIndex = -1;
let pendingDetailKeywordFocusIndex = -1;
let detailKeywordSearchTotal = 0;
let pendingEventsScrollRestoreTop = null;
let pendingLabeledEventFocusId = '';
let detailLoadingWatchdogTimer = 0;

function esc(s){
  return (s ?? '').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function renderColorStyle(colorValue){
  return `--label-color:${esc(colorValue || '#94a3b8')}`;
}

function buildStatusCard(title, copy, tone){
  const kind = tone || 'loading';
  const indicator = kind === 'loading'
    ? '<span class="status-spinner" aria-hidden="true"></span>'
    : `<span class="status-icon ${kind === 'error' ? 'error' : ''}" aria-hidden="true">${kind === 'error' ? '!' : 'i'}</span>`;
  return `<div class="status-card ${esc(kind)}">${indicator}<div class="status-title">${esc(title || '')}</div>${copy ? `<div class="status-copy">${esc(copy)}</div>` : ''}</div>`;
}

function renderInlineStatus(title, copy, tone){
  return `<div class="status-wrap">${buildStatusCard(title, copy, tone)}</div>`;
}

function scheduleBackgroundTask(callback, delayMs){
  const delay = Number.isFinite(delayMs) ? Math.max(0, delayMs) : 0;
  window.setTimeout(() => {
    if(typeof window.requestIdleCallback === 'function'){
      window.requestIdleCallback(() => callback(), { timeout: 1000 });
      return;
    }
    callback();
  }, delay);
}

function setStatusLayer(id, title, copy, tone){
  const layer = document.getElementById(id);
  if(!layer){
    return;
  }
  if(!title){
    layer.classList.add('hidden');
    layer.innerHTML = '';
    return;
  }
  layer.innerHTML = buildStatusCard(title, copy, tone);
  layer.classList.remove('hidden');
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
    // Ignore storage quota errors and keep the in-memory state only.
  }
}

function isCostSummaryCacheFresh(entry){
  return !!entry
    && Number.isFinite(entry.savedAt)
    && entry.savedAt > 0
    && (Date.now() - entry.savedAt) <= COST_SUMMARY_CACHE_MAX_AGE_MS;
}

function setTodayUsageFromCostSummary(data){
  todayUsageSummary = pickTodayUsageSummary(data);
  todayUsageExchangeRate = data && data.exchange_rate ? data.exchange_rate : null;
  todayUsageSummaryStatus = todayUsageSummary ? 'ready' : 'empty';
  todayUsageSummaryMessage = '';
}

function scheduleNextCostSummaryRefresh(delayMs){
  if(costSummaryRefreshTimer){
    clearTimeout(costSummaryRefreshTimer);
  }
  costSummaryRefreshTimer = window.setTimeout(() => {
    costSummaryRefreshTimer = 0;
    scheduleBackgroundTask(() => {
      void loadTodayUsageSummary({ backgroundRefresh: true });
    }, 0);
  }, Math.max(0, delayMs));
}

function updateSessionsBackgroundIndicator(){
  const indicator = document.getElementById('sessions_background_indicator');
  if(!indicator){
    return;
  }
  if(state.isSessionsBackgroundLoading){
    indicator.textContent = t('status.sessions.backgroundInline', {
      loaded: formatNumber(state.sessions.length),
      total: formatNumber(state.sessionsTotalCount || state.sessions.length),
    });
    indicator.setAttribute('title', t('status.sessions.backgroundCopy'));
    indicator.classList.remove('hidden');
    return;
  }
  indicator.textContent = '';
  indicator.removeAttribute('title');
  indicator.classList.add('hidden');
}

function updateReloadButtonState(){
  const button = document.getElementById('reload');
  if(!button){
    return;
  }
  const isManualReload = state.isSessionsLoading && (state.sessionsLoadMode === 'reload' || state.sessionsLoadMode === 'reload_refresh');
  button.disabled = isManualReload;
  button.textContent = isManualReload ? 'Reloading...' : 'Reload';
}

function updateFilterVisibility(){
  const toolbar = document.querySelector('.toolbar');
  const button = document.getElementById('toggle_filters');
  if(filtersVisible){
    toolbar.classList.remove('collapsed');
    button.textContent = t('toolbar.filters.hide');
  } else {
    toolbar.classList.add('collapsed');
    button.textContent = t('toolbar.filters.show');
  }
}

function normalizeLeftPaneTab(value){
  return value === 'labels' ? 'labels' : 'sessions';
}

function normalizeDetailPanelSection(value){
  return value === 'actions' || value === 'search' || value === 'range' ? value : '';
}

function renderLeftPaneTabs(){
  const activeTab = normalizeLeftPaneTab(state.leftPaneTab);
  state.leftPaneTab = activeTab;
  document.querySelectorAll('.left-pane-tab').forEach((button) => {
    const isActive = button.dataset.paneTab === activeTab;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });
  const sessionsPanel = document.getElementById('left_panel_sessions');
  const labelsPanel = document.getElementById('left_panel_labels');
  if(sessionsPanel){
    sessionsPanel.classList.toggle('hidden', activeTab !== 'sessions');
  }
  if(labelsPanel){
    labelsPanel.classList.toggle('hidden', activeTab !== 'labels');
  }
}

function setActiveLeftPaneTab(nextTab){
  const normalized = normalizeLeftPaneTab(nextTab);
  if(state.leftPaneTab === normalized){
    renderLeftPaneTabs();
    if(normalized === 'labels' && !state.hasLoadedLabeled && !state.isLabeledLoading){
      void loadLabeledItems({ mode: 'initial' });
    }
    return;
  }

  state.leftPaneTab = normalized;
  renderLeftPaneTabs();
  saveFiltersSoon();
  if(normalized === 'labels' && !state.hasLoadedLabeled && !state.isLabeledLoading){
    void loadLabeledItems({ mode: 'initial' });
  }
}

function setFiltersVisible(nextVisible){
  filtersVisible = !!nextVisible;
  updateFilterVisibility();
  saveFiltersSoon();
}

function updateDetailActionsVisibility(){
  const actionRow = document.getElementById('detail_action_row');
  const keywordRow = document.getElementById('detail_keyword_row');
  const messageRangeRow = document.getElementById('detail_message_range_row');
  if(!actionRow || !keywordRow || !messageRangeRow){
    return;
  }
  const activeSection = normalizeDetailPanelSection(detailPanelSection);
  detailPanelSection = activeSection;
  actionRow.classList.toggle('hidden', activeSection !== 'actions');
  keywordRow.classList.toggle('hidden', activeSection !== 'search');
  messageRangeRow.classList.toggle('hidden', activeSection !== 'range');
  [
    ['toggle_detail_actions', 'actions', t('detail.actions')],
    ['toggle_detail_search', 'search', t('detail.search')],
    ['toggle_detail_range', 'range', t('detail.range')],
  ].forEach(([id, section, label]) => {
    const button = document.getElementById(id);
    if(!button){
      return;
    }
    const isActive = activeSection === section;
    button.textContent = label;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
}

function setActiveDetailPanelSection(nextSection, options){
  detailPanelSection = normalizeDetailPanelSection(nextSection);
  updateDetailActionsVisibility();
  if(options && options.skipSave){
    return;
  }
  saveFiltersSoon();
}

function setDetailActionsVisible(nextVisible){
  setActiveDetailPanelSection(nextVisible ? 'actions' : '');
}

function toggleDetailPanelSection(section){
  const normalized = normalizeDetailPanelSection(section);
  const nextSection = detailPanelSection === normalized ? '' : normalized;
  setActiveDetailPanelSection(nextSection);
}

function updateCompactUiMode(){
  const nextCompact = window.innerHeight <= 900;
  const changed = nextCompact !== compactUiActive;
  compactUiActive = nextCompact;
  document.documentElement.classList.toggle('compact-ui', compactUiActive);
  if(changed && compactUiActive){
    compactUiSnapshot = {
      filtersVisible,
      detailPanelSection: normalizeDetailPanelSection(detailPanelSection),
    };
    filtersVisible = false;
    updateFilterVisibility();
    detailPanelSection = '';
    updateDetailActionsVisibility();
  } else if(changed && !compactUiActive && compactUiSnapshot){
    filtersVisible = !!compactUiSnapshot.filtersVisible;
    updateFilterVisibility();
    detailPanelSection = normalizeDetailPanelSection(compactUiSnapshot.detailPanelSection);
    updateDetailActionsVisibility();
    compactUiSnapshot = null;
  }
}

function updateDetailMetaVisibility(){
  const meta = document.getElementById('meta');
  const button = document.getElementById('toggle_meta');
  const headerMain = document.querySelector('.header-main');
  if(!meta || !button){
    return;
  }
  const hasContent = meta.textContent.trim() !== '';
  const isHidden = !detailMetaVisible || !hasContent;
  meta.classList.toggle('hidden', isHidden);
  if(headerMain){
    headerMain.classList.toggle('meta-hidden', isHidden);
  }
  button.textContent = detailMetaVisible ? t('header.meta.hide') : t('header.meta.show');
  button.setAttribute('aria-pressed', detailMetaVisible ? 'true' : 'false');
  button.disabled = !hasContent;
}

function setDetailMetaVisible(nextVisible){
  detailMetaVisible = !!nextVisible;
  updateDetailMetaVisibility();
}

function updateLeftPaneVisibility(){
  const container = document.querySelector('.container');
  const mobileButton = document.getElementById('toggle_session_list_mobile');
  const isMobileLayout = window.matchMedia('(max-width: 900px)').matches;
  if(!container){
    return;
  }
  container.classList.toggle('sidebar-collapsed', isMobileLayout && !leftPaneVisible);
  const label = leftPaneVisible ? t('header.list.hide') : t('header.list.show');
  if(mobileButton){
    mobileButton.textContent = leftPaneVisible ? t('header.list.hideShort') : t('header.list.showShort');
    mobileButton.setAttribute('aria-label', label);
    mobileButton.title = label;
  }
}

function setLeftPaneVisible(nextVisible){
  leftPaneVisible = !!nextVisible;
  updateLeftPaneVisibility();
  saveFiltersSoon();
}

function saveFiltersSoon(){
  if(saveFiltersFrame){
    cancelAnimationFrame(saveFiltersFrame);
  }
  saveFiltersFrame = requestAnimationFrame(() => {
    saveFiltersFrame = 0;
    setTimeout(() => {
      saveFilters();
    }, 0);
  });
}

function cancelScheduledSaveFilters(){
  if(saveFiltersFrame){
    cancelAnimationFrame(saveFiltersFrame);
    saveFiltersFrame = 0;
  }
}

function postJson(url, payload){
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  }).then(readJsonResponse);
}

function getSelectedSessionLabelFilter(){
  return document.getElementById('session_label_filter').value || '';
}

function getSelectedListEventLabelFilter(){
  return document.getElementById('event_label_filter').value || '';
}

function getSelectedDetailEventLabelFilter(){
  return document.getElementById('detail_event_label_filter').value || '';
}

function getActiveDetailCostSortMode(){
  const select = document.getElementById('detail_cost_sort');
  const value = select ? select.value : '';
  return value === 'total' || value === 'cost' || value === 'score' ? value : '';
}

function isTurnBoundaryFilterEnabled(){
  const checkbox = document.getElementById('turn_boundary_only');
  return !!(checkbox && checkbox.checked);
}

function isSelectedEventsOnlyFilterEnabled(){
  const checkbox = document.getElementById('selected_events_only');
  return !!(checkbox && checkbox.checked);
}

function isSystemLabeledUserEvent(ev){
  if(!ev || ev.kind !== 'message' || ev.role !== 'user'){
    return false;
  }
  const labels = ev.system_labels || [];
  return labels.includes('SKILLS') || labels.includes('CONTINUATION_SUMMARY') || labels.includes('BACKGROUND_TASK');
}

function shouldShowSystemLabels(){
  return detailMetaVisible;
}

function filterEventsToTurnBoundaries(events){
  if(!Array.isArray(events) || events.length === 0){
    return Array.isArray(events) ? events : [];
  }
  const filtered = [];
  let pendingUser = null;
  let lastAssistant = null;

  function flushTurn(){
    if(!pendingUser){
      return;
    }
    filtered.push(pendingUser);
    if(lastAssistant){
      filtered.push(lastAssistant);
    }
    pendingUser = null;
    lastAssistant = null;
  }

  events.forEach(ev => {
    if(ev.kind !== 'message'){
      return;
    }
    if(ev.role === 'user'){
      if(isSystemLabeledUserEvent(ev)){
        return;
      }
      flushTurn();
      pendingUser = ev;
      return;
    }
    if(ev.role === 'assistant' && pendingUser){
      lastAssistant = ev;
    }
  });

  flushTurn();
  return filtered;
}

function buildLabelOptionText(label){
  return `● ${label.name || ''}`;
}

function updateLabelSelectColor(selectId){
  const select = document.getElementById(selectId);
  if(!select){
    return;
  }
  const label = state.labels.find(item => String(item.id) === String(select.value));
  select.style.color = label && label.color_value ? label.color_value : '';
}

function populateLabelSelect(selectId, allLabel){
  const select = document.getElementById(selectId);
  if(!select){
    return;
  }
  const current = select.value;
  select.innerHTML = '';
  select.add(new Option(allLabel, ''));
  state.labels.forEach(label => {
    const option = new Option(buildLabelOptionText(label), String(label.id));
    if(label && label.color_value){
      option.style.color = label.color_value;
    }
    select.add(option);
  });
  const hasCurrent = state.labels.some(label => String(label.id) === current);
  select.value = hasCurrent ? current : '';
  updateLabelSelectColor(selectId);
}

function populateLabelControls(){
  populateLabelSelect('session_label_filter', t('filter.sessionLabel.all'));
  populateLabelSelect('event_label_filter', t('filter.eventLabel.all'));
  populateLabelSelect('detail_event_label_filter', t('detail.label.all'));
  ['session_label_filter', 'event_label_filter', 'detail_event_label_filter'].forEach(id => {
    const select = document.getElementById(id);
    const pending = select.dataset.pendingValue;
    if(pending && Array.from(select.options).some(option => option.value === pending)){
      select.value = pending;
    }
    delete select.dataset.pendingValue;
    updateLabelSelectColor(id);
  });
  renderSessionList();
  renderSessionLabelStrip();
  renderActiveSession();
  updateSessionLabelButtonState();
}

function resolveLabelsById(ids){
  if(!Array.isArray(ids) || ids.length === 0) return [];
  const map = {};
  for(const l of state.labels) map[l.id] = l;
  return ids.map(id => map[id]).filter(Boolean);
}

function renderAssignedLabels(labels, removeType, extra){
  if(!Array.isArray(labels) || labels.length === 0) return '';
  return labels.map(label => {
    const attrs = removeType ? (
      ` data-remove-type="${esc(removeType)}"` +
      ` data-label-id="${esc(label.id)}"` +
      (extra && extra.eventId ? ` data-event-id="${esc(extra.eventId)}"` : '')
    ) : '';
    const removeButton = removeType
      ? `<button class="label-remove-button" title="${esc(t('picker.removeLabel'))}"${attrs}>×</button>`
      : '';
    return `<span class="data-label-badge" style="${renderColorStyle(label.color_value)}"><span class="label-dot"></span><span>${esc(label.name)}</span>${removeButton}</span>`;
  }).join('');
}

function updateSessionLabelButtonState(){
  const button = document.getElementById('add_session_label');
  button.disabled = !state.activePath || state.labels.length === 0;
}

function renderSessionLabelStrip(){
  const strip = document.getElementById('session_label_strip');
  if(!state.activeSession){
    strip.classList.add('empty');
    strip.textContent = state.isDetailLoading && state.activePath
      ? t('session.labels.loading')
      : t('session.labels.empty');
    updateSessionLabelButtonState();
    return;
  }
  const labels = state.activeSession.session_labels || [];
  if(!labels.length){
    strip.classList.add('empty');
    strip.textContent = t('session.labels.empty');
    updateSessionLabelButtonState();
    return;
  }
  strip.classList.remove('empty');
  strip.innerHTML = renderAssignedLabels(labels, 'session');
  strip.querySelectorAll('.label-remove-button').forEach(button => {
    button.onclick = async () => {
      const labelId = Number(button.dataset.labelId);
      await removeSessionLabel(labelId);
    };
  });
  updateSessionLabelButtonState();
}

function getDetailEventKey(ev, fallbackIndex){
  if(ev && ev.event_id){
    return String(ev.event_id);
  }
  return `${ev && ev.kind ? ev.kind : 'event'}:${ev && ev.timestamp ? ev.timestamp : ''}:${fallbackIndex}`;
}

function getExpandedDetailEventKeySet(path){
  if(!path){
    return null;
  }
  let keys = detailExpandedEventKeysByPath.get(path);
  if(!keys){
    keys = new Set();
    detailExpandedEventKeysByPath.set(path, keys);
  }
  return keys;
}

function isDetailEventBodyExpanded(path, eventKey){
  const keys = path ? detailExpandedEventKeysByPath.get(path) : null;
  if(!keys || !eventKey){
    return false;
  }
  return keys.has(eventKey);
}

function setDetailEventBodyExpanded(path, eventKey, expanded){
  if(!path || !eventKey){
    return;
  }
  const keys = getExpandedDetailEventKeySet(path);
  if(!keys){
    return;
  }
  if(expanded){
    keys.add(eventKey);
  } else {
    keys.delete(eventKey);
  }
}

function buildEventCardHtml(ev, selectedEventLabelId, fallbackIndex, searchMeta){
  const role = ev.role || 'system';
  const roleLabel = role.replaceAll('_', ' ');
  const kindLabel = (ev.kind || 'event').replaceAll('_', ' ');
  const labels = ev.labels || [];
  const systemLabels = ev.system_labels || [];
  const matchesSelectedLabel = selectedEventLabelId && labels.some(label => String(label.id) === selectedEventLabelId);
  const eventKey = getDetailEventKey(ev, fallbackIndex);
  const bodyText = getEventBodyText(ev);
  const eventMatches = searchMeta && searchMeta.matchesByEvent ? (searchMeta.matchesByEvent.get(eventKey) || []) : [];
  const usageHtml = renderUsageBadges(ev.usage, 'event');
  const bodyInner = `${usageHtml ? `<div class="event-usage-row">${usageHtml}</div>` : ''}${ev && ev.kind === 'token_usage' ? renderTokenUsageEventBodyHtml(ev) : `<pre>${renderHighlightedEventBody(bodyText, eventMatches)}</pre>`}`;
  const body = `<div class="ev-body-wrap" data-event-key="${esc(eventKey)}">${bodyInner}<button class="ev-body-toggle">${esc(t('detail.bodyExpand'))}</button></div>`;
  const selectionKey = getEventSelectionKey(ev);
  const isSelectable = state.isEventSelectionMode && isSelectableMessageEvent(ev);
  const isSelected = selectionKey && state.selectedEventIds.has(selectionKey);
  const isRangeSelectable = state.isMessageRangeSelectionMode && isRangeSelectableMessageEvent(ev);
  const isRangeSelected = selectionKey && state.selectedMessageRangeEventId === selectionKey;
  const selectionCheckboxHtml = isSelectable
    ? `<label class="event-select-toggle"><input type="checkbox" class="event-select-checkbox" data-event-id="${esc(selectionKey)}" ${isSelected ? 'checked' : ''} />${esc(t('detail.selectMode'))}</label>`
    : '';
  const rangeSelectionHtml = isRangeSelectable
    ? `<label class="event-range-toggle"><input type="radio" name="message-range-selection" class="event-range-radio" data-event-id="${esc(selectionKey)}" ${isRangeSelected ? 'checked' : ''} />${esc(t('detail.rangeMode'))}</label>`
    : '';
  const labelsHtml = renderAssignedLabels(labels, 'event', { eventId: ev.event_id });
  const copyButtonHtml = getCopyableEventText(ev) && ev.event_id
    ? `<button class="event-copy-button" data-event-id="${esc(ev.event_id || '')}">${esc(t('copy.single'))}</button>`
    : '';
  const systemLabelsHtml = shouldShowSystemLabels()
    ? systemLabels.map(label => `<span class="badge-kind badge-system-label">${esc(label)}</span>`).join('')
    : '';
  return `<div class="ev ${role} ${matchesSelectedLabel ? 'label-match' : ''} ${isSelected ? 'copy-selected' : ''} ${isRangeSelected ? 'range-anchor-selected' : ''}" data-event-id="${esc(ev.event_id || '')}"><div class="ev-head">${selectionCheckboxHtml}${rangeSelectionHtml}<span class="badge-kind">${esc(kindLabel)}</span><span class="badge-role ${role}">${esc(roleLabel)}</span><span class="badge-time">${esc(fmt(ev.timestamp))}</span>${systemLabelsHtml}<span class="event-actions">${labelsHtml}<button class="event-label-add-button" data-event-id="${esc(ev.event_id || '')}" ${state.labels.length ? '' : 'disabled'}>${esc(t('picker.addLabel'))}</button>${copyButtonHtml}</span></div>${body}</div>`;
}

function attachVisibleEventCardHandlers(eventsBox, startIndex, endIndex){
  const wraps = eventsBox.querySelectorAll('.ev-body-wrap');
  const from = typeof startIndex === 'number' ? startIndex : 0;
  const to = typeof endIndex === 'number' ? endIndex : wraps.length;
  for(let i = from; i < to && i < wraps.length; i++){
    const wrap = wraps[i];
    const pre = wrap.querySelector('pre');
    if(!pre) continue;
    const style = getComputedStyle(pre);
    const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.65);
    const threshold = lineHeight * 20 + 20;
    if(pre.scrollHeight > threshold){
      const eventKey = wrap.dataset.eventKey || '';
      const isExpanded = isDetailEventBodyExpanded(state.activePath, eventKey);
      wrap.classList.add('collapsible');
      wrap.classList.toggle('collapsed', !isExpanded);
      const button = wrap.querySelector('.ev-body-toggle');
      if(button){
        button.textContent = isExpanded ? t('detail.bodyCollapse') : t('detail.bodyExpand');
      }
    }
  }
}

let pendingChunkRenderHandle = null;

function cancelPendingChunkRender(){
  if(pendingChunkRenderHandle !== null){
    cancelIdleCallback(pendingChunkRenderHandle);
    pendingChunkRenderHandle = null;
  }
}

const EVENT_RENDER_FIRST_CHUNK = 50;
const EVENT_RENDER_CHUNK_SIZE = 100;

function renderEventList(eventsBox, displayEvents, selectedEventLabelId, searchMeta){
  cancelPendingChunkRender();
  const targetMatch = searchMeta && pendingDetailKeywordFocusIndex >= 0
    ? searchMeta.matches[pendingDetailKeywordFocusIndex] || null
    : null;
  const previousScrollTop = eventsBox.scrollTop;
  const targetScrollTop = Number.isFinite(pendingEventsScrollRestoreTop)
    ? pendingEventsScrollRestoreTop
    : previousScrollTop;

  const firstChunk = displayEvents.slice(0, EVENT_RENDER_FIRST_CHUNK);
  const remaining = displayEvents.slice(EVENT_RENDER_FIRST_CHUNK);

  eventsBox.innerHTML = firstChunk.map((ev, index) => buildEventCardHtml(ev, selectedEventLabelId, index, searchMeta)).join('');
  eventsBox.scrollTop = targetScrollTop;
  attachVisibleEventCardHandlers(eventsBox);

  if(remaining.length > 0){
    let offset = EVENT_RENDER_FIRST_CHUNK;
    function renderNextChunk(deadline){
      pendingChunkRenderHandle = null;
      if(document.getElementById('events') !== eventsBox) return;
      const chunk = remaining.splice(0, EVENT_RENDER_CHUNK_SIZE);
      if(chunk.length === 0) return;
      const fragment = document.createDocumentFragment();
      const temp = document.createElement('div');
      temp.innerHTML = chunk.map((ev, i) => buildEventCardHtml(ev, selectedEventLabelId, offset + i, searchMeta)).join('');
      while(temp.firstChild) fragment.appendChild(temp.firstChild);
      eventsBox.appendChild(fragment);
      attachVisibleEventCardHandlers(eventsBox, offset, offset + chunk.length);
      offset += chunk.length;
      if(remaining.length > 0){
        pendingChunkRenderHandle = requestIdleCallback(renderNextChunk, { timeout: 100 });
      }
    }
    pendingChunkRenderHandle = requestIdleCallback(renderNextChunk, { timeout: 100 });
  }

  if(Number.isFinite(pendingEventsScrollRestoreTop)){
    const lockedScrollTop = pendingEventsScrollRestoreTop;
    pendingEventsScrollRestoreTop = null;
    requestAnimationFrame(() => {
      if(document.getElementById('events') === eventsBox){
        eventsBox.scrollTop = lockedScrollTop;
      }
    });
  }
  if(targetMatch){
    requestAnimationFrame(() => {
      focusDetailKeywordMatch(eventsBox, pendingDetailKeywordFocusIndex);
      pendingDetailKeywordFocusIndex = -1;
    });
  }
}

function hideLabelPicker(){
  const picker = document.getElementById('label_picker');
  picker.classList.add('hidden');
  picker.innerHTML = '';
  labelPickerHandler = null;
}

function showLabelPicker(anchor, onSelect){
  const picker = document.getElementById('label_picker');
  if(!state.labels.length){
    alert(t('picker.noLabels'));
    return;
  }
  labelPickerHandler = onSelect;
  picker.innerHTML = state.labels.map(label =>
    `<button class="label-picker-option" data-label-id="${esc(label.id)}" style="${renderColorStyle(label.color_value)}"><span class="label-dot"></span><span>${esc(label.name)}</span></button>`
  ).join('');
  picker.querySelectorAll('.label-picker-option').forEach(button => {
    button.onclick = async () => {
      const labelId = Number(button.dataset.labelId);
      const handler = labelPickerHandler;
      hideLabelPicker();
      if(!handler){
        return;
      }
      await handler(labelId);
    };
  });
  const rect = anchor.getBoundingClientRect();
  picker.style.top = `${Math.round(rect.bottom + 8)}px`;
  picker.style.left = `${Math.round(Math.min(rect.left, window.innerWidth - 300))}px`;
  picker.classList.remove('hidden');
}

async function loadLabels(reloadSessions){
  const data = await fetchJsonWithTimeout('/api/labels?ts=' + Date.now(), { cache: 'no-store' }, API_REQUEST_TIMEOUT_MS);
  const prev = JSON.stringify(state.labels);
  state.labels = data.labels || [];
  populateLabelControls();
  if(reloadSessions && prev !== JSON.stringify(state.labels)){
    await refreshLabeledViews();
  } else if(shouldReloadLabeledItems()){
    renderLabeledList();
  }
}

function openLabelManagerWindow(){
  const features = 'width=720,height=680,resizable=yes,scrollbars=yes';
  if(labelManagerWindow && !labelManagerWindow.closed){
    labelManagerWindow.focus();
    return;
  }
  labelManagerWindow = window.open(`/labels?lang=${encodeURIComponent(uiLanguage)}`, 'claude_label_manager', features);
}

function openCostSummaryWindow(){
  const features = 'width=1480,height=920,resizable=yes,scrollbars=yes';
  const params = new URLSearchParams({
    lang: uiLanguage,
    currency: getPreferredCostCurrencyCode(),
  });
  window.open(`/costs?${params.toString()}`, 'claude_cost_summary', features);
}

function highlightSessionPath(s){
  const safe = esc(s);
  return safe.replace(/(\d{4}-\d{2}-\d{2}T\d{2}[-:]\d{2}[-:]\d{2}(?:[-:]\d{3,6})?)/g, '<span class="ts">$1</span>');
}

function normalizeSource(source){
  const raw = (source || '').toLowerCase();
  if(raw === 'claude_desktop' || raw === 'desktop') return 'desktop';
  return 'cli';
}

function sourceLabel(source){
  return normalizeSource(source) === 'desktop' ? 'Claude Desktop' : 'Claude Code CLI';
}

function hasSubagentsSegment(session){
  const candidates = [
    session && session.path ? String(session.path) : '',
    session && session.relative_path ? String(session.relative_path) : '',
    session && session.project ? String(session.project) : '',
  ];
  return candidates.some(value => {
    const normalized = String(value || '').toLowerCase();
    return normalized.includes('/subagents/')
      || normalized.includes('\\subagents\\')
      || normalized.includes('/subagents\\')
      || normalized.includes('\\subagents/');
  });
}

function normalizeSourceFilter(source){
  const raw = (source || '').toLowerCase();
  if(raw === '' || raw === 'all') return 'all';
  return raw === 'claude_desktop' || raw === 'desktop' ? 'claude_desktop' : 'claude_cli';
}

function normalizeSubagentsFilter(value){
  const raw = (value || '').toLowerCase();
  if(raw === 'exclude') return 'exclude';
  return 'include';
}

function fmt(ts){
  if(!ts) return '';
  const d = new Date(ts);
  return isNaN(d) ? ts : d.toLocaleString();
}

function getUiLocale(){
  if(uiLanguage === 'zh-Hans') return 'zh-CN';
  if(uiLanguage === 'zh-Hant') return 'zh-TW';
  return uiLanguage || 'ja';
}

function formatUsageCount(value){
  const num = Number(value || 0);
  if(!Number.isFinite(num)) return '0';
  return num.toLocaleString(getUiLocale());
}

function formatUsageUsd(value){
  const num = Number(value);
  if(!Number.isFinite(num)) return '';
  const abs = Math.abs(num);
  const fractionDigits = abs >= 10 ? 2 : (abs >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(num);
}

function formatUsageJpy(value){
  const num = Number(value);
  if(!Number.isFinite(num)) return '';
  const abs = Math.abs(num);
  const fractionDigits = abs >= 100 ? 0 : (abs >= 10 ? 1 : (abs >= 1 ? 2 : 3));
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'JPY',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(num);
}

function formatUsageCny(value){
  const num = Number(value);
  if(!Number.isFinite(num)) return '';
  const abs = Math.abs(num);
  const fractionDigits = abs >= 10 ? 2 : (abs >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(num);
}

function formatLocalCurrency(value, currencyCode){
  const num = Number(value);
  if(!Number.isFinite(num) || !currencyCode || currencyCode === 'USD'){
    return '';
  }
  if(currencyCode === 'JPY'){
    return formatUsageJpy(num);
  }
  if(currencyCode === 'CNY'){
    return formatUsageCny(num);
  }
  const abs = Math.abs(num);
  const fractionDigits = abs >= 10 ? 2 : (abs >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: currencyCode,
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(num);
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
  const fetchedAt = exchangeRate.fetched_at ? fmt(exchangeRate.fetched_at) : '';
  return fetchedAt
    ? `${pair} ${formattedRate} @ ${fetchedAt}`
    : `${pair} ${formattedRate}`;
}

function formatUsageCostDisplay(value, exchangeRate){
  if(!Number.isFinite(Number(value))){
    return t('usage.costUnknown');
  }
  const currencyCode = getPreferredCostCurrencyCode();
  if(currencyCode === 'USD'){
    return formatUsageUsd(value);
  }
  const localValue = convertUsdToLocalCurrency(value, exchangeRate, currencyCode);
  if(localValue == null){
    return formatUsageUsd(value);
  }
  const formattedLocal = formatLocalCurrency(localValue, currencyCode);
  if(!formattedLocal){
    return formatUsageUsd(value);
  }
  return `${formatUsageUsd(value)} / ${formattedLocal}`;
}

function formatUsageMetricWithCostDisplay(tokenValue, costUsd, exchangeRate){
  const normalizedTokenCount = Number(tokenValue || 0);
  const tokenText = formatUsageCount(normalizedTokenCount);
  const numericCost = Number(costUsd);
  if(Number.isFinite(numericCost)){
    if(normalizedTokenCount > 0 || numericCost !== 0){
      return `${tokenText} (${formatUsageCostDisplay(numericCost, exchangeRate)})`;
    }
    return tokenText;
  }
  if(normalizedTokenCount > 0){
    return `${tokenText} (${t('usage.costUnknown')})`;
  }
  return tokenText;
}

function getUsageMetricDisplayParts(tokenValue, costUsd, exchangeRate){
  const normalizedTokenCount = Number(tokenValue || 0);
  const primaryText = formatUsageCount(normalizedTokenCount);
  const numericCost = Number(costUsd);
  const secondaryText = Number.isFinite(numericCost)
    ? (normalizedTokenCount > 0 || numericCost !== 0 ? formatUsageCostDisplay(numericCost, exchangeRate) : '')
    : (normalizedTokenCount > 0 ? t('usage.costUnknown') : '');
  return {
    primaryText,
    secondaryText,
  };
}

function renderPlainUsageMetricValueHtml(value){
  return `<span class="usage-metric-primary">${esc(value)}</span>`;
}

function renderUsageMetricValueHtml(tokenValue, costUsd, exchangeRate){
  const { primaryText, secondaryText } = getUsageMetricDisplayParts(tokenValue, costUsd, exchangeRate);
  return `<span class="usage-metric-primary">${esc(primaryText)}</span>${secondaryText ? ` <span class="usage-metric-secondary">(${esc(secondaryText)})</span>` : ''}`;
}

function renderTokenUsageEventBodyHtml(ev){
  const usage = ev && ev.usage ? ev.usage : null;
  const performance = getUsageCostPerformance(getUsageTotalTokens(usage), usage ? usage.cost_usd : null);
  const lines = [];
  const cacheCreateTokens = usage ? usage.cache_creation_tokens || 0 : 0;
  const cacheReadTokens = usage ? usage.cache_read_tokens || 0 : 0;
  if(ev && ev.model){
    lines.push(`${esc(t('usage.model'))}: ${renderPlainUsageMetricValueHtml(stringifyEventBodyValue(ev.model))}`);
  }
  lines.push(`${esc(t('usage.input'))}: ${renderUsageMetricValueHtml(usage ? usage.input_tokens || 0 : 0, usage ? usage.input_cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${esc(t('usage.cacheCreate'))}: ${renderUsageMetricValueHtml(cacheCreateTokens, usage ? usage.cache_creation_cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${esc(t('usage.cacheRead'))}: ${renderUsageMetricValueHtml(cacheReadTokens, usage ? usage.cache_read_cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${esc(t('usage.output'))}: ${renderUsageMetricValueHtml(usage ? usage.output_tokens || 0 : 0, usage ? usage.output_cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${esc(t('usage.total'))}: ${renderPlainUsageMetricValueHtml(formatUsageCount(getUsageTotalTokens(usage)))}`);
  lines.push(`${esc(t('usage.cost'))}: ${renderPlainUsageMetricValueHtml(formatUsageCostDisplay(usage ? usage.cost_usd : null, state.activeExchangeRate))}`);
  lines.push(`${esc(t('usage.perDollar'))}: ${renderPlainUsageMetricValueHtml(formatTokensPerDollar(getUsageTotalTokens(usage), usage ? usage.cost_usd : null) || '-')}`);
  lines.push(`${esc(t('usage.score'))}: ${renderPlainUsageMetricValueHtml(formatUsageScoreDisplay(performance.score))}`);
  lines.push(`${esc(t('usage.rank'))}: ${renderPlainUsageMetricValueHtml(performance.rank || '-')}`);
  return `<pre class="token-usage-body">${lines.join('\n')}</pre>`;
}

function formatCompactNumber(value){
  const num = Number(value);
  if(!Number.isFinite(num)) return '-';
  return new Intl.NumberFormat(getUiLocale(), {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(num);
}

function getUsageTotalTokens(usage){
  if(!usage) return 0;
  const explicitTotal = Number(usage.total_tokens);
  if(Number.isFinite(explicitTotal)) return explicitTotal;
  return Number(usage.input_tokens || 0)
    + Number(usage.output_tokens || 0)
    + Number(usage.cache_creation_tokens || 0)
    + Number(usage.cache_read_tokens || 0);
}

function getUsageCachedTokens(usage){
  if(!usage) return 0;
  return Number(usage.cache_creation_tokens || 0) + Number(usage.cache_read_tokens || 0);
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
  if(score >= 6.5){
    return { score, rank: 'SS' };
  }
  if(score >= 6.0){
    return { score, rank: 'S' };
  }
  if(score >= 5.5){
    return { score, rank: 'A' };
  }
  if(score >= 5.0){
    return { score, rank: 'B' };
  }
  if(score >= 4.5){
    return { score, rank: 'C' };
  }
  if(score >= 4.0){
    return { score, rank: 'D' };
  }
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

function hasUsageData(usage){
  if(!usage) return false;
  return getUsageTotalTokens(usage) > 0 || Number.isFinite(Number(usage.cost_usd));
}

function buildUsageBadge(label, extraClass){
  return `<span class="usage-badge${extraClass ? ` ${extraClass}` : ''}">${esc(label)}</span>`;
}

function renderUsageBadges(usage, variant){
  if(!hasUsageData(usage)) return '';
  const isCompact = variant === 'session';
  const badges = [
    buildUsageBadge(
      t('usage.totalTokens', { count: formatUsageCount(getUsageTotalTokens(usage)) }),
      'usage-badge-strong'
    ),
  ];
  if(!isCompact){
    badges.push(buildUsageBadge(t('usage.inputTokens', { count: formatUsageCount(usage.input_tokens) })));
    badges.push(buildUsageBadge(t('usage.outputTokens', { count: formatUsageCount(usage.output_tokens) })));
    if(Number(usage.cache_creation_tokens || 0) > 0){
      badges.push(buildUsageBadge(t('usage.cacheCreateTokens', { count: formatUsageCount(usage.cache_creation_tokens) })));
    }
    if(Number(usage.cache_read_tokens || 0) > 0){
      badges.push(buildUsageBadge(t('usage.cacheReadTokens', { count: formatUsageCount(usage.cache_read_tokens) })));
    }
  }
  if(Number.isFinite(Number(usage.cost_usd))){
    badges.push(buildUsageBadge(t('usage.costUsd', { cost: formatUsageCostDisplay(usage.cost_usd, state.activeExchangeRate) }), 'usage-badge-cost'));
  }
  return badges.join('');
}

function renderUsageMetaRows(usage){
  if(!hasUsageData(usage)) return '';
  const performance = getUsageCostPerformance(getUsageTotalTokens(usage), usage.cost_usd);
  const cacheCreateTokens = Number(usage.cache_creation_tokens || 0);
  const cacheReadTokens = Number(usage.cache_read_tokens || 0);
  const metrics = [
    {
      label: t('usage.input'),
      valueHtml: renderUsageMetricValueHtml(usage.input_tokens, usage.input_cost_usd, state.activeExchangeRate),
      tooltip: t('usage.tooltip.input'),
    },
    {
      label: t('usage.cacheCreate'),
      valueHtml: renderUsageMetricValueHtml(cacheCreateTokens, usage.cache_creation_cost_usd, state.activeExchangeRate),
      tooltip: t('usage.tooltip.cacheCreate'),
    },
    {
      label: t('usage.cacheRead'),
      valueHtml: renderUsageMetricValueHtml(cacheReadTokens, usage.cache_read_cost_usd, state.activeExchangeRate),
      tooltip: t('usage.tooltip.cacheRead'),
    },
    {
      label: t('usage.output'),
      valueHtml: renderUsageMetricValueHtml(usage.output_tokens, usage.output_cost_usd, state.activeExchangeRate),
      tooltip: t('usage.tooltip.output'),
    },
    {
      label: t('usage.total'),
      valueHtml: renderPlainUsageMetricValueHtml(formatUsageCount(getUsageTotalTokens(usage))),
      tooltip: t('usage.tooltip.total'),
    },
    {
      label: t('usage.cost'),
      valueHtml: renderPlainUsageMetricValueHtml(formatUsageCostDisplay(usage.cost_usd, state.activeExchangeRate)),
      tooltip: t('usage.tooltip.cost'),
    },
    {
      label: t('usage.perDollar'),
      valueHtml: renderPlainUsageMetricValueHtml(formatTokensPerDollar(getUsageTotalTokens(usage), usage.cost_usd) || '-'),
      tooltip: t('usage.tooltip.perDollar'),
    },
    {
      label: t('usage.score'),
      valueHtml: renderPlainUsageMetricValueHtml(formatUsageScoreDisplay(performance.score)),
      tooltip: t('usage.tooltip.score'),
    },
    {
      label: t('usage.rank'),
      valueHtml: renderPlainUsageMetricValueHtml(performance.rank || '-'),
      tooltip: t('usage.tooltip.rank'),
    },
  ];
  return `<div class="header-meta-row"><span class="header-meta-label">${esc(t('meta.usage'))}</span><div class="usage-meta-items">${metrics.map(({ label, valueHtml, tooltip }) => `<span class="usage-metric" data-tooltip="${esc(tooltip)}" tabindex="0" aria-label="${esc(`${label}: ${tooltip}`)}"><span class="meta-tag">${esc(`${label}:`)}</span><span class="header-meta-text usage-metric-value">${valueHtml}</span></span>`).join('')}</div></div>`;
}

function renderModelMetaRow(session){
  if(!session) return '';
  const models = Array.isArray(session.models)
    ? session.models.filter(Boolean)
    : [];
  const effortLevel = session && session.effort_level
    ? String(session.effort_level).trim()
    : '';
  let modelRow = '';
  if(models.length){
    const effortTag = effortLevel
      ? `<span class="meta-tag">${esc(`${t('meta.effortLevel')}:`)}</span><span class="header-meta-text">${esc(effortLevel)}</span>`
      : '';
    modelRow = `<div class="header-meta-row"><span class="header-meta-label">${esc(t('meta.models'))}</span><span class="header-meta-value">${esc(models.join(', '))}</span>${effortTag}</div>`;
  } else if(session.model){
    const effortTag = effortLevel
      ? `<span class="meta-tag">${esc(`${t('meta.effortLevel')}:`)}</span><span class="header-meta-text">${esc(effortLevel)}</span>`
      : '';
    modelRow = `<div class="header-meta-row"><span class="header-meta-label">${esc(t('meta.models'))}</span><span class="header-meta-value">${esc(session.model)}</span>${effortTag}</div>`;
  } else if(effortLevel){
    modelRow = `<div class="header-meta-row"><span class="header-meta-label">${esc(t('meta.effortLevel'))}</span><span class="header-meta-value">${esc(effortLevel)}</span></div>`;
  }

  const fxDisplay = formatExchangeRateDisplay(state.activeExchangeRate);
  const fxRow = fxDisplay
    ? `<div class="header-meta-row"><span class="header-meta-label">${esc(t('meta.fxRate'))}</span><span class="header-meta-value">${esc(fxDisplay)}</span></div>`
    : '';
  return modelRow + fxRow;
}

function findCostSummaryPeriod(summary, groupKey, periodKey, scopeKey){
  if(!summary || !Array.isArray(summary.groups)){
    return null;
  }
  const group = summary.groups.find(item => item && item.key === groupKey);
  if(!group){
    return null;
  }
  const periods = Array.isArray(group[scopeKey]) ? group[scopeKey] : [];
  return periods.find(item => item && item.key === periodKey) || null;
}

function pickTodayUsageSummary(summary){
  const tokenEventPeriod = findCostSummaryPeriod(summary, 'day', 'today', 'token_usage_events');
  if(tokenEventPeriod && Number(tokenEventPeriod.item_count || 0) > 0){
    return { ...tokenEventPeriod, scope: 'tokenEvent' };
  }
  const sessionPeriod = findCostSummaryPeriod(summary, 'day', 'today', 'sessions');
  if(sessionPeriod && Number(sessionPeriod.item_count || 0) > 0){
    return { ...sessionPeriod, scope: 'session' };
  }
  return null;
}

function renderTodayUsageSummary(){
  const box = document.getElementById('today_usage_summary');
  if(!box){
    return;
  }

  if(todayUsageSummaryStatus === 'loading' && !todayUsageSummary){
    box.innerHTML = `<div class="today-usage-card"><span class="today-usage-title">${esc(t('todayUsage.title'))}</span><span class="today-usage-placeholder">${esc(t('todayUsage.loading'))}</span></div>`;
    return;
  }

  if(!todayUsageSummary){
    const toneClass = todayUsageSummaryStatus === 'error' ? ' error' : '';
    const text = todayUsageSummaryStatus === 'error'
      ? (todayUsageSummaryMessage || t('todayUsage.error'))
      : (todayUsageSummaryStatus === 'empty' ? t('todayUsage.empty') : t('todayUsage.loading'));
    box.innerHTML = `<div class="today-usage-card"><span class="today-usage-title">${esc(t('todayUsage.title'))}</span><span class="today-usage-placeholder${toneClass}">${esc(text)}</span></div>`;
    return;
  }

  const performance = getUsageCostPerformance(todayUsageSummary.total_tokens || 0, todayUsageSummary.cost_usd);
  const metrics = [
    {
      label: t('usage.total'),
      value: formatUsageCount(todayUsageSummary.total_tokens || 0),
      tooltip: t('usage.tooltip.total'),
    },
    {
      label: t('usage.cost'),
      value: formatUsageCostDisplay(todayUsageSummary.cost_usd, todayUsageExchangeRate),
      tooltip: t('usage.tooltip.cost'),
    },
    {
      label: t('usage.score'),
      value: formatUsageScoreDisplay(performance.score),
      tooltip: t('usage.tooltip.score'),
    },
    {
      label: t('usage.rank'),
      value: performance.rank || '-',
      tooltip: t('usage.tooltip.rank'),
    },
  ];
  box.innerHTML = `<div class="today-usage-card"><span class="today-usage-title">${esc(t('todayUsage.title'))}</span><div class="today-usage-items">${metrics.map(({ label, value, tooltip }) => `<span class="usage-metric" data-tooltip="${esc(tooltip)}" tabindex="0" aria-label="${esc(`${label}: ${tooltip}`)}"><span class="meta-tag">${esc(`${label}:`)}</span><span class="header-meta-text usage-metric-value">${esc(value)}</span></span>`).join('')}</div></div>`;
}

async function loadTodayUsageSummary(options){
  const opts = options || {};
  const cached = !opts.forceRefresh ? readCostSummaryCache() : null;
  const hasCachedData = !!(cached && cached.data);
  const hasExistingData = !!todayUsageSummary;

  if(hasCachedData){
    setTodayUsageFromCostSummary(cached.data);
    renderTodayUsageSummary();
  } else if(!opts.backgroundRefresh || !hasExistingData){
    todayUsageSummaryStatus = 'loading';
    todayUsageSummaryMessage = '';
    renderTodayUsageSummary();
  }

  const shouldFetchNow = !!opts.forceRefresh || !hasCachedData || !isCostSummaryCacheFresh(cached);
  if(!shouldFetchNow){
    scheduleNextCostSummaryRefresh(COST_SUMMARY_PRELOAD_INTERVAL_MS);
    return;
  }

  const requestId = ++todayUsageSummaryRequestSeq;
  try {
    const params = new URLSearchParams();
    params.set('ts', Date.now().toString());
    if(opts.forceRefresh){
      params.set('force', '1');
    }
    const data = await fetchJsonWithTimeout(`/api/cost-summary?${params.toString()}`, { cache: 'no-store' }, API_REQUEST_TIMEOUT_MS);
    if(requestId !== todayUsageSummaryRequestSeq){
      return;
    }
    writeCostSummaryCache(data);
    setTodayUsageFromCostSummary(data);
  } catch (error) {
    if(requestId !== todayUsageSummaryRequestSeq){
      return;
    }
    if(!hasCachedData && !hasExistingData){
      todayUsageExchangeRate = null;
      todayUsageSummaryStatus = 'error';
      todayUsageSummaryMessage = normalizeRequestError(error, t('todaySummary.error'));
    }
  } finally {
    if(requestId !== todayUsageSummaryRequestSeq){
      return;
    }
    renderTodayUsageSummary();
    scheduleNextCostSummaryRefresh(COST_SUMMARY_PRELOAD_INTERVAL_MS);
  }
}

function scheduleTodayUsageSummaryRefresh(){
  if(todayUsageSummaryTimer){
    clearTimeout(todayUsageSummaryTimer);
  }
  todayUsageSummaryTimer = setTimeout(() => {
    todayUsageSummaryTimer = 0;
    loadTodayUsageSummary();
  }, 90);
}

function toTimestamp(ts){
  if(!ts) return NaN;
  const d = new Date(ts);
  return d.getTime();
}

function parseOptionalDateStart(raw){
  if(!raw) return null;
  const iso = parseDateInputToIso(raw);
  if(!iso) return null;
  const ts = toTimestamp(`${iso}T00:00:00`);
  return Number.isNaN(ts) ? null : ts;
}

function parseOptionalDateEnd(raw){
  if(!raw) return null;
  const iso = parseDateInputToIso(raw);
  if(!iso) return null;
  const ts = toTimestamp(`${iso}T23:59:59.999`);
  return Number.isNaN(ts) ? null : ts;
}

function pad2(value){
  return String(value).padStart(2, '0');
}

function parseDateInputToIso(raw){
  if(typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  if(!trimmed) return '';
  const canonical = trimmed
    .replace(/　/g, ' ')
    .replace(/[年月]/g, '/')
    .replace(/日/g, ' ')
    .replace(/[．。]/g, '.')
    .replace(/\s*\/\s*/g, '/')
    .replace(/\s+/g, ' ');
  let m = canonical.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if(!m){
    m = canonical.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);
  }
  if(!m){
    m = canonical.match(/(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})/);
  }
  if(!m){
    return '';
  }
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if(!Number.isFinite(year) || year < 1900 || year > 2999) return '';
  if(!Number.isFinite(month) || month < 1 || month > 12) return '';
  if(!Number.isFinite(day) || day < 1 || day > 31) return '';
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  if(d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day){
    return '';
  }
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

function formatDateInputFromIso(isoValue){
  const iso = parseDateInputToIso(isoValue);
  if(!iso) return '';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if(!m) return '';
  return `${m[1]} / ${m[2]} / ${m[3]}`;
}

function normalizeDateInputDisplay(raw){
  const iso = parseDateInputToIso(raw);
  return iso ? formatDateInputFromIso(iso) : '';
}

function parseDateTimeInputToIso(raw){
  if(typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  if(!trimmed) return '';
  const canonical = trimmed
    .replace(/　/g, ' ')
    .replace(/[年月]/g, '/')
    .replace(/日/g, ' ')
    .replace(/[：]/g, ':')
    .replace(/[．。]/g, '.')
    .replace(/\s*\/\s*/g, '/')
    .replace(/\s+/g, ' ');
  let m = canonical.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2})?$/);
  if(!m){
    m = canonical.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2}) (\d{1,2}):(\d{2})(?::\d{1,2})?$/);
  }
  if(!m){
    m = canonical.match(/(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})[ T](\d{1,2}):(\d{1,2})(?::\d{1,2})?/);
  }
  if(!m){
    return '';
  }
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const hour = Number(m[4]);
  const minute = Number(m[5]);
  if(!Number.isFinite(year) || year < 1900 || year > 2999) return '';
  if(!Number.isFinite(month) || month < 1 || month > 12) return '';
  if(!Number.isFinite(day) || day < 1 || day > 31) return '';
  if(!Number.isFinite(hour) || hour < 0 || hour > 23) return '';
  if(!Number.isFinite(minute) || minute < 0 || minute > 59) return '';
  const d = new Date(year, month - 1, day, hour, minute, 0, 0);
  if(
    d.getFullYear() !== year ||
    d.getMonth() !== month - 1 ||
    d.getDate() !== day ||
    d.getHours() !== hour ||
    d.getMinutes() !== minute
  ){
    return '';
  }
  return `${year}-${pad2(month)}-${pad2(day)}T${pad2(hour)}:${pad2(minute)}`;
}

function formatDateTimeInputFromIso(isoValue){
  const iso = parseDateTimeInputToIso(isoValue);
  if(!iso) return '';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
  if(!m) return '';
  return `${m[1]} / ${m[2]} / ${m[3]} ${m[4]}:${m[5]}`;
}

function normalizeDatetimeInputDisplay(raw){
  const iso = parseDateTimeInputToIso(raw);
  return iso ? formatDateTimeInputFromIso(iso) : '';
}

function parseTimeInputToValue(raw){
  if(typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  if(!trimmed) return '';
  const canonical = trimmed
    .replace(/[：]/g, ':')
    .replace(/\s+/g, '');
  const m = canonical.match(/^(\d{1,2}):(\d{2})(?::\d{1,2})?$/);
  if(!m){
    return '';
  }
  const hour = Number(m[1]);
  const minute = Number(m[2]);
  if(!Number.isFinite(hour) || hour < 0 || hour > 23) return '';
  if(!Number.isFinite(minute) || minute < 0 || minute > 59) return '';
  return `${pad2(hour)}:${pad2(minute)}`;
}

function buildDateTimeIsoFromParts(dateRaw, timeRaw, boundary){
  const dateIso = parseDateInputToIso(dateRaw);
  if(!dateIso){
    return '';
  }
  const timeValue = parseTimeInputToValue(timeRaw);
  const fallbackTime = boundary === 'end' ? '23:59' : '00:00';
  return `${dateIso}T${timeValue || fallbackTime}`;
}

function extractTimeInputFromIso(isoValue){
  const iso = parseDateTimeInputToIso(isoValue);
  if(!iso) return '';
  const m = iso.match(/T(\d{2}):(\d{2})$/);
  if(!m) return '';
  return `${m[1]}:${m[2]}`;
}

function applyDatePasteValue(input, raw){
  if(!input){
    return false;
  }
  const dateIso = parseDateInputToIso(raw);
  if(!dateIso){
    return false;
  }
  const seg = segInstances[input.id];
  if(seg){
    seg.setFromIso(dateIso);
  } else {
    input.value = dateIso;
  }
  const fp = fpInstances[input.id];
  if(fp) fp.setDate(dateIso, false);
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

function applyDateTimePairPasteValue(dateInput, timeInput, target, raw){
  if(!dateInput || !timeInput || !target){
    return false;
  }
  const dateTimeIso = parseDateTimeInputToIso(raw);
  if(dateTimeIso){
    const dateVal = parseDateInputToIso(dateTimeIso);
    const timeVal = extractTimeInputFromIso(dateTimeIso);
    const dateSeg = segInstances[dateInput.id];
    const timeSeg = segInstances[timeInput.id];
    if(dateSeg) dateSeg.setFromIso(dateVal);
    else dateInput.value = dateVal;
    if(timeSeg) timeSeg.setFromValue(timeVal);
    else timeInput.value = timeVal;
    const fp = fpInstances[dateInput.id];
    if(fp) fp.setDate(dateVal, false);
    syncDateTimeInputPairState(dateInput.id, timeInput.id);
    target.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }
  if(target === dateInput){
    const dateIso = parseDateInputToIso(raw);
    if(!dateIso){
      return false;
    }
    const dateSeg = segInstances[dateInput.id];
    if(dateSeg) dateSeg.setFromIso(dateIso);
    else dateInput.value = dateIso;
    const fp = fpInstances[dateInput.id];
    if(fp) fp.setDate(dateIso, false);
    syncDateTimeInputPairState(dateInput.id, timeInput.id);
    dateInput.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }
  const timeValue = parseTimeInputToValue(raw);
  if(!timeValue || !parseDateInputToIso(dateInput.value)){
    return false;
  }
  const timeSeg = segInstances[timeInput.id];
  if(timeSeg) timeSeg.setFromValue(timeValue);
  else timeInput.value = timeValue;
  syncDateTimeInputPairState(dateInput.id, timeInput.id);
  timeInput.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

function setDateTimePairFromIso(dateId, timeId, isoValue){
  const dateVal = parseDateInputToIso(isoValue);
  const timeVal = extractTimeInputFromIso(isoValue);
  setFpDateTimeValue(dateId, timeId, dateVal, timeVal);
  syncDateTimeInputPairState(dateId, timeId);
}

function syncDateTimeInputPairState(dateId, timeId){
  const dateInput = document.getElementById(dateId);
  const timeInput = document.getElementById(timeId);
  if(!dateInput || !timeInput){
    return;
  }
  const requiresActiveSession = dateId.startsWith('detail_');
  const hasControlAccess = !requiresActiveSession || !!state.activeSession;
  const hasDate = Boolean(parseDateInputToIso(dateInput.value));
  if(!hasDate){
    const timeSeg = segInstances[timeId];
    if(timeSeg) timeSeg.setFromValue('');
    else timeInput.value = '';
  } else if(timeInput.value){
    timeInput.value = parseTimeInputToValue(timeInput.value);
  }
  const dateSeg = segInstances[dateId];
  const timeSeg = segInstances[timeId];
  if(dateSeg){
    if(!hasControlAccess) dateSeg.wrap.classList.add('disabled');
    else dateSeg.wrap.classList.remove('disabled');
    dateSeg.segs.forEach(s => { s.disabled = !hasControlAccess; });
    if(dateSeg.icon) dateSeg.icon.disabled = !hasControlAccess;
  } else {
    dateInput.disabled = !hasControlAccess;
  }
  if(timeSeg){
    const timeDisabled = !hasControlAccess || !hasDate;
    if(timeDisabled) timeSeg.wrap.classList.add('disabled');
    else timeSeg.wrap.classList.remove('disabled');
    timeSeg.segs.forEach(s => { s.disabled = timeDisabled; });
    const spinBtns = timeSeg.wrap.querySelectorAll('.seg-spin button');
    spinBtns.forEach(b => { b.disabled = timeDisabled; });
    timeInput.disabled = timeDisabled;
  } else {
    timeInput.disabled = !hasControlAccess || !hasDate;
  }
}

function refreshDateTimeInputPairStates(){
  syncDateTimeInputPairState('event_date_from_date', 'event_date_from_time');
  syncDateTimeInputPairState('event_date_to_date', 'event_date_to_time');
  syncDateTimeInputPairState('detail_event_date_from_date', 'detail_event_date_from_time');
  syncDateTimeInputPairState('detail_event_date_to_date', 'detail_event_date_to_time');
}

const DATETIME_INPUT_SKELETON = '0000 / 00 / 00 --:--';
const DATETIME_INPUT_SEGMENTS = [
  { start: 0, end: 4, fill: '0' },
  { start: 7, end: 9, fill: '0' },
  { start: 12, end: 14, fill: '0' },
  { start: 15, end: 17, fill: '-' },
  { start: 18, end: 20, fill: '-' },
];

function getDateTimeSegmentIndexByPos(pos){
  const safePos = Number.isFinite(pos) ? pos : 0;
  for(let i = 0; i < DATETIME_INPUT_SEGMENTS.length; i += 1){
    const seg = DATETIME_INPUT_SEGMENTS[i];
    if(safePos >= seg.start && safePos <= seg.end){
      return i;
    }
  }
  if(safePos < DATETIME_INPUT_SEGMENTS[0].start){
    return 0;
  }
  return DATETIME_INPUT_SEGMENTS.length - 1;
}

function selectDateTimeSegment(input, index){
  const safeIndex = Math.max(0, Math.min(DATETIME_INPUT_SEGMENTS.length - 1, index));
  const seg = DATETIME_INPUT_SEGMENTS[safeIndex];
  input.setSelectionRange(seg.start, seg.end);
}

function setDateTimeSegment(inputValue, index, segmentValue){
  const seg = DATETIME_INPUT_SEGMENTS[index];
  return inputValue.slice(0, seg.start) + segmentValue + inputValue.slice(seg.end);
}

function shiftDateTimeSegment(currentSegment, digit, fillChar){
  const len = currentSegment.length;
  const normalized = currentSegment.replace(/[^0-9]/g, '').padStart(len, fillChar === '-' ? '0' : fillChar).slice(-len);
  const shifted = normalized.slice(1) + digit;
  if(fillChar === '-'){
    const allZero = /^0+$/.test(shifted);
    if(allZero){
      return '-'.repeat(len);
    }
  }
  return shifted;
}

function setupDateTimeSegmentInput(input){
  if(!input || input.dataset.segmentedReady === '1'){
    return;
  }
  input.dataset.segmentedReady = '1';
  const ensureSkeleton = () => {
    if(!input.value){
      input.value = DATETIME_INPUT_SKELETON;
    }
  };
  input.addEventListener('focus', () => {
    ensureSkeleton();
    selectDateTimeSegment(input, getDateTimeSegmentIndexByPos(input.selectionStart || 0));
  });
  input.addEventListener('click', () => {
    ensureSkeleton();
    selectDateTimeSegment(input, getDateTimeSegmentIndexByPos(input.selectionStart || 0));
  });
  input.addEventListener('keydown', (event) => {
    if(!/^\d$/.test(event.key) && event.key !== 'Backspace' && event.key !== 'Delete' && event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Tab' && event.key !== '/' && event.key !== ':' && event.key !== ' '){
      return;
    }
    ensureSkeleton();
    let segmentIndex = getDateTimeSegmentIndexByPos(input.selectionStart || 0);
    if(/^\d$/.test(event.key)){
      event.preventDefault();
      const seg = DATETIME_INPUT_SEGMENTS[segmentIndex];
      const current = input.value.slice(seg.start, seg.end);
      const next = shiftDateTimeSegment(current, event.key, seg.fill);
      input.value = setDateTimeSegment(input.value, segmentIndex, next);
      selectDateTimeSegment(input, segmentIndex);
      return;
    }
    if(event.key === 'Backspace' || event.key === 'Delete'){
      event.preventDefault();
      const seg = DATETIME_INPUT_SEGMENTS[segmentIndex];
      input.value = setDateTimeSegment(input.value, segmentIndex, seg.fill.repeat(seg.end - seg.start));
      selectDateTimeSegment(input, segmentIndex);
      return;
    }
    if(event.key === 'ArrowLeft'){
      event.preventDefault();
      selectDateTimeSegment(input, Math.max(0, segmentIndex - 1));
      return;
    }
    if(event.key === 'ArrowRight' || event.key === '/' || event.key === ':' || event.key === ' '){
      event.preventDefault();
      selectDateTimeSegment(input, Math.min(DATETIME_INPUT_SEGMENTS.length - 1, segmentIndex + 1));
      return;
    }
    if(event.key === 'Tab'){
      if(event.shiftKey){
        selectDateTimeSegment(input, Math.max(0, segmentIndex - 1));
      } else {
        selectDateTimeSegment(input, Math.min(DATETIME_INPUT_SEGMENTS.length - 1, segmentIndex + 1));
      }
    }
  });
  input.addEventListener('blur', () => {
    const display = normalizeDatetimeInputDisplay(input.value);
    if(display){
      input.value = display;
      return;
    }
    if(input.value === DATETIME_INPUT_SKELETON){
      input.value = '';
    }
  });
  input.addEventListener('input', (event) => {
    if(event && typeof event.inputType === 'string' && event.inputType !== 'insertFromPaste'){
      return;
    }
    const iso = parseDateTimeInputToIso(input.value || '');
    if(!iso){
      return;
    }
    input.value = formatDateTimeInputFromIso(iso);
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  input.addEventListener('paste', (event) => {
    const text = event.clipboardData ? event.clipboardData.getData('text') : '';
    const iso = parseDateTimeInputToIso(text || '');
    if(iso){
      event.preventDefault();
      input.value = formatDateTimeInputFromIso(iso);
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    setTimeout(() => {
      const fallbackIso = parseDateTimeInputToIso(input.value || '');
      if(!fallbackIso){
        return;
      }
      input.value = formatDateTimeInputFromIso(fallbackIso);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }, 0);
  });
}

const DATE_INPUT_SKELETON = '0000 / 00 / 00';
const DATE_INPUT_SEGMENTS = [
  { start: 0, end: 4, fill: '0' },
  { start: 7, end: 9, fill: '0' },
  { start: 12, end: 14, fill: '0' },
];

function getDateSegmentIndexByPos(pos){
  const safePos = Number.isFinite(pos) ? pos : 0;
  for(let i = 0; i < DATE_INPUT_SEGMENTS.length; i += 1){
    const seg = DATE_INPUT_SEGMENTS[i];
    if(safePos >= seg.start && safePos <= seg.end){
      return i;
    }
  }
  if(safePos < DATE_INPUT_SEGMENTS[0].start){
    return 0;
  }
  return DATE_INPUT_SEGMENTS.length - 1;
}

function selectDateSegment(input, index){
  const safeIndex = Math.max(0, Math.min(DATE_INPUT_SEGMENTS.length - 1, index));
  const seg = DATE_INPUT_SEGMENTS[safeIndex];
  input.setSelectionRange(seg.start, seg.end);
}

function setDateSegment(inputValue, index, segmentValue){
  const seg = DATE_INPUT_SEGMENTS[index];
  return inputValue.slice(0, seg.start) + segmentValue + inputValue.slice(seg.end);
}

function setupDateSegmentInput(input){
  if(!input || input.dataset.segmentedDateReady === '1'){
    return;
  }
  input.dataset.segmentedDateReady = '1';
  const ensureSkeleton = () => {
    if(!input.value){
      input.value = DATE_INPUT_SKELETON;
    }
  };
  input.addEventListener('focus', () => {
    ensureSkeleton();
    selectDateSegment(input, getDateSegmentIndexByPos(input.selectionStart || 0));
  });
  input.addEventListener('click', () => {
    ensureSkeleton();
    selectDateSegment(input, getDateSegmentIndexByPos(input.selectionStart || 0));
  });
  input.addEventListener('keydown', (event) => {
    if(!/^\d$/.test(event.key) && event.key !== 'Backspace' && event.key !== 'Delete' && event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Tab' && event.key !== '/' && event.key !== ' '){
      return;
    }
    ensureSkeleton();
    const segmentIndex = getDateSegmentIndexByPos(input.selectionStart || 0);
    if(/^\d$/.test(event.key)){
      event.preventDefault();
      const seg = DATE_INPUT_SEGMENTS[segmentIndex];
      const current = input.value.slice(seg.start, seg.end);
      const next = shiftDateTimeSegment(current, event.key, seg.fill);
      input.value = setDateSegment(input.value, segmentIndex, next);
      selectDateSegment(input, segmentIndex);
      return;
    }
    if(event.key === 'Backspace' || event.key === 'Delete'){
      event.preventDefault();
      const seg = DATE_INPUT_SEGMENTS[segmentIndex];
      input.value = setDateSegment(input.value, segmentIndex, seg.fill.repeat(seg.end - seg.start));
      selectDateSegment(input, segmentIndex);
      return;
    }
    if(event.key === 'ArrowLeft'){
      event.preventDefault();
      selectDateSegment(input, Math.max(0, segmentIndex - 1));
      return;
    }
    if(event.key === 'ArrowRight' || event.key === '/' || event.key === ' '){
      event.preventDefault();
      selectDateSegment(input, Math.min(DATE_INPUT_SEGMENTS.length - 1, segmentIndex + 1));
      return;
    }
    if(event.key === 'Tab'){
      if(event.shiftKey){
        selectDateSegment(input, Math.max(0, segmentIndex - 1));
      } else {
        selectDateSegment(input, Math.min(DATE_INPUT_SEGMENTS.length - 1, segmentIndex + 1));
      }
    }
  });
  input.addEventListener('blur', () => {
    const display = normalizeDateInputDisplay(input.value);
    if(display){
      input.value = display;
      return;
    }
    if(input.value === DATE_INPUT_SKELETON){
      input.value = '';
    }
  });
  input.addEventListener('input', (event) => {
    if(event && typeof event.inputType === 'string' && event.inputType !== 'insertFromPaste'){
      return;
    }
    const iso = parseDateInputToIso(input.value || '');
    if(!iso){
      return;
    }
    input.value = formatDateInputFromIso(iso);
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  input.addEventListener('paste', (event) => {
    const text = event.clipboardData ? event.clipboardData.getData('text') : '';
    const iso = parseDateInputToIso(text || '');
    if(iso){
      event.preventDefault();
      input.value = formatDateInputFromIso(iso);
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    setTimeout(() => {
      const fallbackIso = parseDateInputToIso(input.value || '');
      if(!fallbackIso){
        return;
      }
      input.value = formatDateInputFromIso(fallbackIso);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }, 0);
  });
}

function destroyDateTimePickers(){
  dateTimePickers.forEach((picker) => {
    try {
      picker.destroy();
    } catch (e) {
      // Ignore cleanup errors.
    }
  });
  dateTimePickers = [];
}

function destroyDatePickers(){
  datePickers.forEach((picker) => {
    try {
      picker.destroy();
    } catch (e) {
      // Ignore cleanup errors.
    }
  });
  datePickers = [];
}

function bindCalendarTriggerButtons(){
  document.querySelectorAll('.datetime-trigger').forEach((button) => {
    const target = button.getAttribute('data-target') || '';
    button.onclick = () => {
      const allPickers = datePickers.concat(dateTimePickers);
      const picker = allPickers.find((item) => item && item.input && item.input.id === target);
      if(picker){
        picker.open();
      }
    };
  });
}

function initDatePickers(){
  destroyDatePickers();
  if(typeof flatpickr !== 'function'){
    return;
  }
  const attachPicker = (inputId, onApply) => {
    const input = document.getElementById(inputId);
    if(!input) return;
    setupDateSegmentInput(input);
    const normalized = normalizeDateInputDisplay(input.value);
    if(input.value !== normalized){
      input.value = normalized;
    }
    let beforeOpenValue = input.value;
    const picker = flatpickr(input, {
      enableTime: false,
      dateFormat: 'Y / m / d',
      allowInput: true,
      clickOpens: false,
      onOpen: () => {
        beforeOpenValue = normalizeDateInputDisplay(input.value);
      },
      onClose: () => {
        const currentValue = normalizeDateInputDisplay(input.value);
        if(input.value !== currentValue){
          input.value = currentValue;
        }
        if(currentValue !== beforeOpenValue){
          onApply();
        }
      },
    });
    datePickers.push(picker);
  };
  attachPicker('date_from', applyFilter);
  attachPicker('date_to', applyFilter);
  bindCalendarTriggerButtons();
}

function initDateTimePickers(){
  destroyDateTimePickers();
  if(typeof flatpickr !== 'function'){
    return;
  }
  const confirmPluginFactory = window.confirmDatePlugin;
  const createPlugins = () => {
    if(typeof confirmPluginFactory !== 'function'){
      return [];
    }
    return [confirmPluginFactory({ confirmText: 'OK', showAlways: true, theme: 'light' })];
  };
  const attachPicker = (inputId, onApply) => {
    const input = document.getElementById(inputId);
    if(!input) return;
    setupDateTimeSegmentInput(input);
    const normalized = normalizeDatetimeInputDisplay(input.value);
    if(input.value !== normalized){
      input.value = normalized;
    }
    let beforeOpenValue = input.value;
    const picker = flatpickr(input, {
      enableTime: true,
      time_24hr: true,
      enableSeconds: false,
      minuteIncrement: 1,
      dateFormat: 'Y / m / d H:i',
      allowInput: true,
      clickOpens: false,
      plugins: createPlugins(),
      onOpen: () => {
        beforeOpenValue = normalizeDatetimeInputDisplay(input.value);
      },
      onReady: (_selectedDates, _dateStr, instance) => {
        const calendar = instance.calendarContainer;
        if(!calendar) return;
        const actions = document.createElement('div');
        actions.className = 'flatpickr-extra-actions';
        const clearButton = document.createElement('button');
        clearButton.type = 'button';
        clearButton.textContent = '削除';
        clearButton.addEventListener('click', () => {
          instance.clear();
          instance.close();
        });
        const todayButton = document.createElement('button');
        todayButton.type = 'button';
        todayButton.textContent = '今日';
        todayButton.addEventListener('click', () => {
          instance.setDate(new Date(), false);
          instance.close();
        });
        actions.appendChild(clearButton);
        actions.appendChild(todayButton);
        const confirmRow = calendar.querySelector('.flatpickr-confirm');
        if(confirmRow && confirmRow.parentNode){
          confirmRow.parentNode.insertBefore(actions, confirmRow);
        } else {
          calendar.appendChild(actions);
        }
      },
      onClose: () => {
        const currentValue = normalizeDatetimeInputDisplay(input.value);
        if(input.value !== currentValue){
          input.value = currentValue;
        }
        if(currentValue !== beforeOpenValue){
          onApply();
        }
      },
    });
    dateTimePickers.push(picker);
  };
  attachPicker('event_date_from', applyFilter);
  attachPicker('event_date_to', applyFilter);
  attachPicker('detail_event_date_from', () => {
    saveFilters();
    renderActiveSession();
  });
  attachPicker('detail_event_date_to', () => {
    saveFilters();
    renderActiveSession();
  });
  bindCalendarTriggerButtons();
}

function parseOptionalDatetimeStart(raw){
  if(!raw) return null;
  const iso = parseDateTimeInputToIso(raw);
  if(!iso) return null;
  const ts = toTimestamp(iso);
  return Number.isNaN(ts) ? null : ts;
}

function parseOptionalDatetimeEnd(raw){
  if(!raw) return null;
  const iso = parseDateTimeInputToIso(raw);
  if(!iso) return null;
  const ts = toTimestamp(iso);
  if(Number.isNaN(ts)) return null;
  return ts + 59999;
}

function getActiveSessionId(){
  if(!state.activeSession) return '';
  return (state.activeSession.session_id || state.activeSession.id || '').toString().trim();
}

function getButtonLabel(button, fallback){
  if(!button) return fallback || '';
  if(!button.dataset.defaultLabel){
    button.dataset.defaultLabel = button.textContent;
  }
  return button.dataset.defaultLabel || fallback || '';
}

function flashButtonLabel(button, temporaryLabel, fallback, duration){
  if(!button) return;
  const defaultLabel = getButtonLabel(button, fallback);
  button.textContent = temporaryLabel;
  if(button._labelTimer){
    clearTimeout(button._labelTimer);
  }
  button._labelTimer = setTimeout(() => {
    button.textContent = defaultLabel;
  }, duration || BUTTON_FEEDBACK_MS);
}

function waitForUiFeedback(duration){
  return new Promise(resolve => {
    setTimeout(resolve, duration || BUTTON_FEEDBACK_MS);
  });
}

function getDetailKeywordInputValue(){
  const input = document.getElementById('detail_keyword_q');
  return input ? input.value : '';
}

function stringifyEventBodyValue(value){
  if(value == null){
    return '';
  }
  if(typeof value === 'string'){
    return value;
  }
  if(typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint'){
    return String(value);
  }
  try {
    return JSON.stringify(value, (key, currentValue) => {
      if(typeof currentValue === 'string' && currentValue.startsWith('data:image/')){
        return '[image data omitted]';
      }
      return currentValue;
    }, 2) || '';
  } catch (error) {
    return String(value);
  }
}

function containsLiteralKeyword(text, keyword){
  if(!keyword){
    return false;
  }
  return stringifyEventBodyValue(text).toLocaleLowerCase().includes(keyword.toLocaleLowerCase());
}

function findLiteralKeywordRanges(text, keyword){
  if(!keyword){
    return [];
  }
  const source = stringifyEventBodyValue(text);
  const haystack = source.toLocaleLowerCase();
  const needle = keyword.toLocaleLowerCase();
  const ranges = [];
  let cursor = 0;
  while(cursor <= haystack.length - needle.length){
    const nextIndex = haystack.indexOf(needle, cursor);
    if(nextIndex === -1){
      break;
    }
    ranges.push({ start: nextIndex, end: nextIndex + keyword.length });
    cursor = nextIndex + Math.max(keyword.length, 1);
  }
  return ranges;
}

function getEventBodyText(ev){
  if(!ev){
    return '';
  }
  if(ev.kind === 'message' || ev.kind === 'agent_update'){
    return stringifyEventBodyValue(ev.text);
  }
  if(ev.kind === 'token_usage'){
    return formatTokenUsageEventBody(ev);
  }
  if(ev.kind === 'function_call'){
    return `name: ${stringifyEventBodyValue(ev.name)}
${stringifyEventBodyValue(ev.arguments)}`;
  }
  if(ev.kind === 'function_output'){
    return stringifyEventBodyValue(ev.output);
  }
  try {
    return JSON.stringify(ev, null, 2) || '';
  } catch (error) {
    return '';
  }
}

function formatTokenUsageEventBody(ev){
  const usage = ev && ev.usage ? ev.usage : null;
  const performance = getUsageCostPerformance(getUsageTotalTokens(usage), usage ? usage.cost_usd : null);
  const lines = [];
  const cacheCreateTokens = usage ? usage.cache_creation_tokens || 0 : 0;
  const cacheReadTokens = usage ? usage.cache_read_tokens || 0 : 0;
  if(ev && ev.model){
    lines.push(`${t('usage.model')}: ${stringifyEventBodyValue(ev.model)}`);
  }
  lines.push(`${t('usage.input')}: ${formatUsageMetricWithCostDisplay(usage ? usage.input_tokens || 0 : 0, usage ? usage.input_cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${t('usage.cacheCreate')}: ${formatUsageMetricWithCostDisplay(cacheCreateTokens, usage ? usage.cache_creation_cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${t('usage.cacheRead')}: ${formatUsageMetricWithCostDisplay(cacheReadTokens, usage ? usage.cache_read_cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${t('usage.output')}: ${formatUsageMetricWithCostDisplay(usage ? usage.output_tokens || 0 : 0, usage ? usage.output_cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${t('usage.total')}: ${formatUsageCount(getUsageTotalTokens(usage))}`);
  lines.push(`${t('usage.cost')}: ${formatUsageCostDisplay(usage ? usage.cost_usd : null, state.activeExchangeRate)}`);
  lines.push(`${t('usage.perDollar')}: ${formatTokensPerDollar(getUsageTotalTokens(usage), usage ? usage.cost_usd : null) || '-'}`);
  lines.push(`${t('usage.score')}: ${formatUsageScoreDisplay(performance.score)}`);
  lines.push(`${t('usage.rank')}: ${performance.rank || '-'}`);
  return lines.join('\n');
}

function getCopyableEventText(ev){
  const text = getEventBodyText(ev);
  return text && text.trim() ? text : '';
}

function buildDetailKeywordSearchMeta(displayEvents, keyword){
  const matches = [];
  const matchesByEvent = new Map();
  const rawKeyword = keyword || '';
  if(!rawKeyword){
    return { keyword: '', matches, matchesByEvent, total: 0 };
  }
  displayEvents.forEach((ev, eventIndex) => {
    const eventKey = getDetailEventKey(ev, eventIndex);
    const ranges = findLiteralKeywordRanges(getEventBodyText(ev), rawKeyword);
    if(!ranges.length){
      return;
    }
    const eventMatches = ranges.map(range => {
      const match = {
        eventKey,
        eventIndex,
        start: range.start,
        end: range.end,
        globalIndex: matches.length,
      };
      matches.push(match);
      return match;
    });
    matchesByEvent.set(eventKey, eventMatches);
  });
  return {
    keyword: rawKeyword,
    matches,
    matchesByEvent,
    total: matches.length,
  };
}

function normalizeDetailKeywordSearchPosition(searchMeta){
  if(!searchMeta.total){
    detailKeywordCurrentMatchIndex = -1;
    pendingDetailKeywordFocusIndex = -1;
    return;
  }
  if(detailKeywordCurrentMatchIndex < 0 || detailKeywordCurrentMatchIndex >= searchMeta.total){
    detailKeywordCurrentMatchIndex = 0;
  }
  if(pendingDetailKeywordFocusIndex >= searchMeta.total){
    pendingDetailKeywordFocusIndex = -1;
  }
}

function renderHighlightedEventBody(text, eventMatches){
  if(!Array.isArray(eventMatches) || !eventMatches.length){
    return esc(text || '');
  }
  let cursor = 0;
  let html = '';
  const source = text || '';
  eventMatches.forEach(match => {
    html += esc(source.slice(cursor, match.start));
    const currentClass = match.globalIndex === detailKeywordCurrentMatchIndex ? ' current' : '';
    html += `<mark class="detail-keyword-hit${currentClass}" data-search-match-index="${match.globalIndex}">${esc(source.slice(match.start, match.end))}</mark>`;
    cursor = match.end;
  });
  html += esc(source.slice(cursor));
  return html;
}

function updateDetailKeywordControls(searchMeta){
  const input = document.getElementById('detail_keyword_q');
  const filterButton = document.getElementById('detail_keyword_filter');
  const searchButton = document.getElementById('detail_keyword_search');
  const prevButton = document.getElementById('detail_keyword_prev');
  const nextButton = document.getElementById('detail_keyword_next');
  const clearButton = document.getElementById('detail_keyword_clear');
  if(!input || !filterButton || !searchButton || !prevButton || !nextButton || !clearButton){
    return;
  }
  const hasActiveSession = !!state.activeSession;
  const hasInputValue = getDetailKeywordInputValue() !== '';
  const searchTotal = searchMeta && typeof searchMeta.total === 'number' ? searchMeta.total : detailKeywordSearchTotal;
  const hasSearchMatches = searchTotal > 0;
  const hasKeywordState = hasInputValue || detailKeywordFilterTerm !== '' || detailKeywordSearchTerm !== '';
  input.disabled = !hasActiveSession;
  const hasActiveFilter = detailKeywordFilterTerm !== '';
  filterButton.disabled = !hasActiveSession || (!hasInputValue && !hasActiveFilter);
  searchButton.disabled = !hasActiveSession || !hasInputValue;
  prevButton.disabled = !hasSearchMatches;
  nextButton.disabled = !hasSearchMatches;
  clearButton.disabled = !hasKeywordState;
  filterButton.classList.toggle('active', hasActiveSession && hasActiveFilter);
  filterButton.textContent = hasActiveFilter ? t('detail.searchFilterClear') : t('detail.searchFilter');
  searchButton.classList.toggle('active', hasActiveSession && detailKeywordSearchTerm !== '');
  const matchCountEl = document.getElementById('detail_keyword_match_count');
  if(matchCountEl){
    if(hasSearchMatches){
      const current = detailKeywordCurrentMatchIndex >= 0 ? detailKeywordCurrentMatchIndex + 1 : 0;
      matchCountEl.textContent = t('detail.matchCounter', { current: current, total: searchTotal });
      matchCountEl.classList.remove('hidden');
    } else {
      matchCountEl.textContent = '';
      matchCountEl.classList.add('hidden');
    }
  }
  updateClearDetailButtonState();
}

function updateDetailDisplayControlsState(){
  const hasActiveSession = !!state.activeSession;
  ['only_user_instruction', 'only_ai_response', 'turn_boundary_only', 'only_token_usage', 'reverse_order'].forEach((id) => {
    const input = document.getElementById(id);
    const label = input ? input.closest('.toggle-chip') : null;
    if(input){
      input.disabled = !hasActiveSession;
    }
    if(label){
      label.classList.toggle('disabled', !hasActiveSession);
      label.setAttribute('aria-disabled', hasActiveSession ? 'false' : 'true');
    }
  });
  const detailEventLabelFilter = document.getElementById('detail_event_label_filter');
  if(detailEventLabelFilter){
    detailEventLabelFilter.disabled = !hasActiveSession;
  }
  const detailCostSort = document.getElementById('detail_cost_sort');
  if(detailCostSort){
    detailCostSort.disabled = !hasActiveSession;
  }
  syncDateTimeInputPairState('detail_event_date_from_date', 'detail_event_date_from_time');
  syncDateTimeInputPairState('detail_event_date_to_date', 'detail_event_date_to_time');
  const clearDetailEventDateButton = document.getElementById('clear_detail_event_date');
  if(clearDetailEventDateButton){
    clearDetailEventDateButton.disabled = !hasActiveSession || !hasDetailEventDateFilter();
  }
}

function resetDetailKeywordState(){
  detailKeywordFilterTerm = '';
  detailKeywordSearchTerm = '';
  detailKeywordCurrentMatchIndex = -1;
  pendingDetailKeywordFocusIndex = -1;
  detailKeywordSearchTotal = 0;
}

function focusDetailKeywordMatch(eventsBox, matchIndex){
  if(matchIndex < 0){
    return;
  }
  const target = eventsBox.querySelector(`.detail-keyword-hit[data-search-match-index="${matchIndex}"]`);
  if(target){
    target.scrollIntoView({ block: 'center', inline: 'nearest' });
  }
}

function focusEventCard(eventId){
  if(!eventId){
    return false;
  }
  const target = Array.from(document.querySelectorAll('#events .ev')).find(node => node.dataset.eventId === eventId);
  if(!target){
    return false;
  }
  target.scrollIntoView({ block: 'center', inline: 'nearest' });
  target.classList.add('label-navigation-hit');
  window.setTimeout(() => {
    target.classList.remove('label-navigation-hit');
  }, 1600);
  return true;
}

async function openLabeledEvent(path, eventId){
  pendingLabeledEventFocusId = eventId || '';
  clearDetailFilters();
  await openSession(path, { mode: 'open', preserveLabeledFocus: true });
  focusEventCard(eventId);
}

function isAutomaticSessionsLoadMode(mode){
  return mode === 'auto' || mode === 'focus';
}

function shouldSyncActiveSessionAfterListLoad(mode){
  return mode === 'labels' || mode === 'reload' || mode === 'clear' || mode === 'initial';
}

function clearDeferredDetailSyncTimer(){
  if(deferredDetailSyncTimer){
    clearTimeout(deferredDetailSyncTimer);
    deferredDetailSyncTimer = 0;
  }
}

function noteDetailInteraction(){
  detailInteractionLockUntil = Date.now() + DETAIL_INTERACTION_LOCK_MS;
}

function hasDetailTextSelection(){
  const eventsBox = document.getElementById('events');
  const selection = window.getSelection ? window.getSelection() : null;
  if(!eventsBox || !selection || selection.isCollapsed || selection.rangeCount === 0){
    return false;
  }
  const anchorNode = selection.anchorNode;
  const focusNode = selection.focusNode;
  return Boolean(
    (anchorNode && eventsBox.contains(anchorNode)) ||
    (focusNode && eventsBox.contains(focusNode))
  );
}

function hasRecentDetailInteraction(){
  return detailPointerDown || hasDetailTextSelection() || Date.now() < detailInteractionLockUntil;
}

function syncActiveSessionSummaryFromList(path){
  if(!path){
    return;
  }
  const summary = (state.sessions || []).find(session => session.path === path);
  if(!summary){
    return;
  }
  state.activeSession = {
    ...(state.activeSession || {}),
    ...summary,
  };
}

async function maybeRunDeferredAutomaticDetailSync(){
  if(!pendingAutomaticDetailSync){
    return;
  }
  if(!document.hasFocus() || hasRecentDetailInteraction() || state.isDetailLoading || !state.activePath){
    scheduleDeferredAutomaticDetailSync();
    return;
  }
  pendingAutomaticDetailSync = false;
  clearDeferredDetailSyncTimer();
  await openSession(state.activePath, { mode: 'sync' });
}

function scheduleDeferredAutomaticDetailSync(){
  clearDeferredDetailSyncTimer();
  if(!pendingAutomaticDetailSync){
    return;
  }
  const waitMs = Math.max(0, detailInteractionLockUntil - Date.now()) + 80;
  deferredDetailSyncTimer = setTimeout(() => {
    deferredDetailSyncTimer = 0;
    void maybeRunDeferredAutomaticDetailSync();
  }, waitMs);
}

async function copyTextToClipboard(text){
  if(!text) return false;
  let copied = false;
  try {
    if(navigator.clipboard && navigator.clipboard.writeText){
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch (e) {
    copied = false;
  }
  if(copied){
    return true;
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.opacity = '0';
  document.body.appendChild(helper);
  helper.select();
  try {
    copied = document.execCommand('copy');
  } finally {
    document.body.removeChild(helper);
  }
  return copied;
}

function getEventSelectionKey(ev){
  return ev && ev.event_id ? String(ev.event_id) : '';
}

function getDisplayCopyableEvents(){
  return getDisplayEvents().filter(ev => !!getCopyableEventText(ev));
}

function isCopyableMessageEvent(ev){
  return ev && ev.kind === 'message' && !!getCopyableEventText(ev);
}

function isSelectableMessageEvent(ev){
  return !!getEventSelectionKey(ev);
}

function isRangeSelectableMessageEvent(ev){
  if(!getEventSelectionKey(ev)){
    return false;
  }
  if(ev && ev.kind === 'token_usage'){
    return true;
  }
  return isCopyableMessageEvent(ev);
}

function getSelectableDisplayMessageEvents(){
  return getDisplayEvents().filter(isSelectableMessageEvent);
}

function getSelectableDisplayRangeEvents(){
  return getDisplayEvents().filter(isRangeSelectableMessageEvent);
}

function getSelectedMessageEvents(){
  const selectedIds = state.selectedEventIds || new Set();
  return (state.activeEvents || []).filter(ev => isSelectableMessageEvent(ev) && selectedIds.has(getEventSelectionKey(ev)));
}

function getSelectedMessageRangeEvent(){
  const selectedId = state.selectedMessageRangeEventId || '';
  if(!selectedId){
    return null;
  }
  return (state.activeEvents || []).find(ev => isRangeSelectableMessageEvent(ev) && getEventSelectionKey(ev) === selectedId) || null;
}

function clearSelectedEventIds(){
  state.selectedEventIds = new Set();
  const checkbox = document.getElementById('selected_events_only');
  if(checkbox){
    checkbox.checked = false;
  }
}

function syncSelectedEventIdsToActiveEvents(){
  const validIds = new Set((state.activeEvents || []).filter(isSelectableMessageEvent).map(getEventSelectionKey));
  state.selectedEventIds = new Set(Array.from(state.selectedEventIds || []).filter(id => validIds.has(id)));
  if(state.selectedEventIds.size === 0){
    const checkbox = document.getElementById('selected_events_only');
    if(checkbox){
      checkbox.checked = false;
    }
  }
}

function clearMessageRangeSelection(){
  state.isMessageRangeSelectionMode = false;
  state.selectedMessageRangeEventId = '';
  state.detailMessageRangeMode = '';
}

function syncSelectedMessageRangeToActiveEvents(){
  if(!state.selectedMessageRangeEventId){
    return;
  }
  if(getSelectedMessageRangeEvent()){
    return;
  }
  state.selectedMessageRangeEventId = '';
  state.detailMessageRangeMode = '';
}

function updateDisplayedMessagesCopyButtonState(){
  const button = document.getElementById('copy_displayed_messages');
  if(!state.activeSession){
    button.disabled = true;
    return;
  }
  const hasMessages = !!getDisplayCopyableEvents().length;
  button.disabled = state.isDetailLoading || !hasMessages;
}

function updateCopyResumeButtonState(){
  const button = document.getElementById('copy_resume_command');
  button.disabled = !getActiveSessionId();
}

function updateEventSelectionModeButtonState(){
  const button = document.getElementById('event_selection_mode');
  if(!button){
    return;
  }
  const hasSelectableMessages = !!getSelectableDisplayMessageEvents().length;
  const hasSelectedMessages = !!getSelectedMessageEvents().length;
  button.disabled = !state.activeSession || (!hasSelectableMessages && !hasSelectedMessages && !state.isEventSelectionMode);
  button.textContent = state.isEventSelectionMode ? t('detail.selectEnd') : t('detail.selectMode');
  button.classList.toggle('selection-active', state.isEventSelectionMode);
}

function updateSelectedEventsOnlyToggleState(){
  const input = document.getElementById('selected_events_only');
  const label = input ? input.closest('.toggle-chip') : null;
  if(!input){
    return;
  }
  const hasActiveSession = !!state.activeSession;
  const hasSelectedMessages = !!getSelectedMessageEvents().length;
  const enabled = hasActiveSession && hasSelectedMessages;
  if(!enabled && input.checked){
    input.checked = false;
  }
  input.disabled = !enabled;
  if(label){
    label.classList.toggle('disabled', !enabled);
    label.setAttribute('aria-disabled', enabled ? 'false' : 'true');
  }
}

function updateCopySelectedMessagesButtonState(){
  const button = document.getElementById('copy_selected_messages');
  if(!button){
    return;
  }
  const selectedMessages = getSelectedMessageEvents();
  const defaultLabel = selectedMessages.length
    ? t('detail.copySelectedCount', { count: selectedMessages.length })
    : t('detail.copySelected');
  button.disabled = state.isDetailLoading || selectedMessages.length === 0;
  button.textContent = defaultLabel;
  button.dataset.defaultLabel = defaultLabel;
}

function updateMessageRangeSelectionModeButtonState(){
  const button = document.getElementById('message_range_selection_mode');
  if(!button){
    return;
  }
  const hasSelectableMessages = !!getSelectableDisplayRangeEvents().length;
  const hasSelectedMessage = !!getSelectedMessageRangeEvent();
  button.disabled = !state.activeSession || (!hasSelectableMessages && !hasSelectedMessage && !state.isMessageRangeSelectionMode);
  button.textContent = state.isMessageRangeSelectionMode ? t('detail.rangeModeEnd') : t('detail.rangeMode');
  button.classList.toggle('selection-active', state.isMessageRangeSelectionMode);
}

function updateClearMessageRangeSelectionButtonState(){
  const button = document.getElementById('clear_message_range_selection');
  if(!button){
    return;
  }
  button.disabled = !state.activeSession || (!getSelectedMessageRangeEvent() && !state.detailMessageRangeMode);
}

function updateMessageRangeFilterButtonsState(){
  const afterButton = document.getElementById('detail_message_range_after');
  const beforeButton = document.getElementById('detail_message_range_before');
  if(!afterButton || !beforeButton){
    return;
  }
  const hasSelectedMessage = !!getSelectedMessageRangeEvent();
  const isAfterActive = state.detailMessageRangeMode === 'after';
  const isBeforeActive = state.detailMessageRangeMode === 'before';
  const hasActiveRangeMode = isAfterActive || isBeforeActive;
  afterButton.disabled = state.isDetailLoading || !hasSelectedMessage;
  beforeButton.disabled = state.isDetailLoading || !hasSelectedMessage;
  afterButton.classList.toggle('active', isAfterActive);
  beforeButton.classList.toggle('active', isBeforeActive);
  afterButton.classList.toggle('contrast-dim', hasActiveRangeMode && !isAfterActive);
  beforeButton.classList.toggle('contrast-dim', hasActiveRangeMode && !isBeforeActive);
  afterButton.textContent = isAfterActive ? t('detail.rangeAfterActive') : t('detail.rangeAfter');
  beforeButton.textContent = isBeforeActive ? t('detail.rangeBeforeActive') : t('detail.rangeBefore');
  afterButton.setAttribute('aria-pressed', isAfterActive ? 'true' : 'false');
  beforeButton.setAttribute('aria-pressed', isBeforeActive ? 'true' : 'false');
}

function updateRefreshDetailButtonState(){
  const button = document.getElementById('refresh_detail');
  const isManualRefresh = state.isDetailLoading && state.detailLoadMode === 'refresh';
  button.disabled = !state.activePath || isManualRefresh;
  if(!isManualRefresh){
    button.textContent = t('detail.refresh');
    return;
  }
  button.textContent = t('detail.refreshing');
}

function hasDetailFilter(){
  return Boolean(
    document.getElementById('only_user_instruction').checked ||
    document.getElementById('only_ai_response').checked ||
    document.getElementById('turn_boundary_only').checked ||
    document.getElementById('only_token_usage').checked ||
    document.getElementById('selected_events_only').checked ||
    document.getElementById('reverse_order').checked ||
    getActiveDetailCostSortMode() ||
    getSelectedDetailEventLabelFilter() ||
    state.detailMessageRangeMode ||
    getDetailKeywordInputValue() ||
    detailKeywordFilterTerm !== '' ||
    detailKeywordSearchTerm !== '' ||
    state.isEventSelectionMode ||
    ((state.selectedEventIds && state.selectedEventIds.size) || 0) > 0 ||
    state.isMessageRangeSelectionMode ||
    state.selectedMessageRangeEventId ||
    getFpDateValue('detail_event_date_from_date') ||
    document.getElementById('detail_event_date_from_time').value ||
    getFpDateValue('detail_event_date_to_date') ||
    document.getElementById('detail_event_date_to_time').value
  );
}

function hasDetailEventDateFilter(){
  return Boolean(
    getFpDateValue('detail_event_date_from_date') ||
    document.getElementById('detail_event_date_from_time').value ||
    getFpDateValue('detail_event_date_to_date') ||
    document.getElementById('detail_event_date_to_time').value
  );
}

function updateClearDetailButtonState(){
  const button = document.getElementById('clear_detail');
  if(!button){
    return;
  }
  button.disabled = !state.activeSession || !hasDetailFilter();
}

function hasListFilter(){
  return Boolean(
    document.getElementById('cwd_q').value.trim() ||
    getFpDateValue('date_from') ||
    getFpDateValue('date_to') ||
    getFpDateValue('event_date_from_date') ||
    document.getElementById('event_date_from_time').value ||
    getFpDateValue('event_date_to_date') ||
    document.getElementById('event_date_to_time').value ||
    document.getElementById('q').value.trim() ||
    normalizeSourceFilter(document.getElementById('source_filter').value || 'all') !== 'all' ||
    normalizeSubagentsFilter(document.getElementById('subagents_filter').value || 'include') !== 'include' ||
    getSelectedSessionLabelFilter() ||
    getSelectedListEventLabelFilter()
  );
}

async function copyResumeCommand(){
  const sessionId = getActiveSessionId();
  if(!sessionId) return;

  const commandText = 'claude --resume ' + sessionId;
  const copied = await copyTextToClipboard(commandText);

  if(copied){
    const button = document.getElementById('copy_resume_command');
    flashButtonLabel(button, t('copy.copied'), t('detail.copyResume'));
  }
}

function scheduleLoadSessions(){
  saveFilters();
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
  }
  loadSessionsTimer = setTimeout(() => {
    loadSessionsTimer = null;
    loadSessions();
  }, SEARCH_DEBOUNCE_MS);
}

function normalizeRequestError(error, fallback){
  if(error && typeof error.message === 'string' && error.message.trim()){
    return error.message.trim();
  }
  return fallback;
}

function clearDetailLoadingWatchdog(){
  if(detailLoadingWatchdogTimer){
    clearTimeout(detailLoadingWatchdogTimer);
    detailLoadingWatchdogTimer = 0;
  }
}

function scheduleDetailLoadingWatchdog(path, requestId){
  clearDetailLoadingWatchdog();
  detailLoadingWatchdogTimer = setTimeout(() => {
    if(!state.isDetailLoading){
      return;
    }
    if(requestId !== loadSessionDetailRequestSeq){
      return;
    }
    if(path !== state.activePath){
      return;
    }
    state.isDetailLoading = false;
    state.detailLoadMode = '';
    if(!state.detailError){
      state.detailError = `Detail loading timed out after ${Math.ceil(API_REQUEST_TIMEOUT_MS / 1000)}s`;
    }
    renderActiveSession();
  }, API_REQUEST_TIMEOUT_MS + 1500);
}

async function fetchJsonWithTimeout(url, options, timeoutMs){
  const timeout = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : API_REQUEST_TIMEOUT_MS;
  const controller = new AbortController();
  const requestOptions = options ? { ...options, signal: controller.signal } : { signal: controller.signal };
  const timer = setTimeout(() => {
    controller.abort();
  }, timeout);
  try {
    const response = await fetch(url, requestOptions);
    return await readJsonResponse(response);
  } catch (error) {
    if(error && error.name === 'AbortError'){
      throw new Error(`Request timed out after ${Math.ceil(timeout / 1000)}s`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function readJsonResponse(response){
  const contentType = String(response && response.headers && response.headers.get('content-type') || '').toLowerCase();
  if(contentType.includes('application/json')){
    const data = await response.json();
    if(response.ok){
      return data;
    }
    const errorMessage = data && typeof data.error === 'string' && data.error.trim()
      ? data.error.trim()
      : `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`;
    throw new Error(errorMessage);
  }

  const rawText = await response.text();
  const snippet = (rawText || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  const statusText = `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`;
  throw new Error(snippet ? `${statusText}: ${snippet}` : statusText);
}

function getActiveSortOrder(){
  const active = document.querySelector('.sort-tab.active');
  return active ? active.dataset.sort : 'desc';
}

function setActiveSortOrder(value){
  document.querySelectorAll('.sort-tab').forEach(tab => {
    const isActive = tab.dataset.sort === value;
    tab.classList.toggle('active', isActive);
    tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });
}

async function fetchSessionVersion(path){
  return await fetchJsonWithTimeout('/api/session-version?path=' + encodeURIComponent(path) + '&ts=' + Date.now(), { cache: 'no-store' }, API_REQUEST_TIMEOUT_MS);
}

function shouldUseProgressiveInitialSessionLoad(loadMode){
  return loadMode === 'initial';
}

function buildSessionListRequestParams(options){
  const opts = options || {};
  const params = new URLSearchParams();
  params.set('ts', Date.now().toString());

  if(typeof opts.offset === 'number' && Number.isFinite(opts.offset) && opts.offset >= 0){
    params.set('offset', String(opts.offset));
  }

  if(typeof opts.limit === 'number' && Number.isFinite(opts.limit) && opts.limit > 0){
    params.set('limit', String(opts.limit));
  }

  const q = typeof opts.q === 'string' ? opts.q.trim() : '';
  if(q){
    params.set('q', q);
    params.set('mode', opts.searchMode === 'or' ? 'or' : 'and');
  }

  if(opts.sessionLabelId){
    params.set('session_label_id', opts.sessionLabelId);
  }

  if(opts.eventLabelId){
    params.set('event_label_id', opts.eventLabelId);
  }

  if(opts.sortOrder && opts.sortOrder !== 'desc'){
    params.set('sort', opts.sortOrder);
  }

  if(opts.force){
    params.set('force', '1');
  }

  return params;
}

function shouldUseFilteredSessionsEndpoint(q, sessionLabelId, eventLabelId){
  return !!(q || sessionLabelId || eventLabelId);
}

function getSessionListTotalCount(data, fallbackCount){
  const totalCount = Number(data && data.total_count);
  return Number.isFinite(totalCount) && totalCount >= 0 ? totalCount : fallbackCount;
}

function appendLoadedSessions(sessions){
  const previousFilteredCount = state.filtered.length;
  state.sessions = state.sessions.concat(sessions);
  const filters = getSessionListFilterState();
  const appendedFiltered = sessions.filter(session => matchesSessionListFilters(session, filters));
  state.filtered = state.filtered.concat(appendedFiltered);
  renderSessionList({ appendSessions: appendedFiltered, previousFilteredCount });
}

async function fetchSessionListChunk(requestId, requestOptions, offset){
  const params = buildSessionListRequestParams({
    q: requestOptions.q,
    searchMode: requestOptions.searchMode,
    sessionLabelId: requestOptions.sessionLabelId,
    eventLabelId: requestOptions.eventLabelId,
    sortOrder: requestOptions.sortOrder,
    offset,
  });
  const endpoint = shouldUseFilteredSessionsEndpoint(
    requestOptions.q,
    requestOptions.sessionLabelId,
    requestOptions.eventLabelId
  ) ? '/api/sessions' : '/api/sessions-lite';
  const data = await fetchJsonWithTimeout(endpoint + '?' + params.toString(), { cache: 'no-store' }, API_REQUEST_TIMEOUT_MS);
  if(requestId !== loadSessionsRequestSeq){
    return null;
  }
  return data;
}

async function loadSessionsProgressively(requestId, requestOptions){
  const firstData = await fetchSessionListChunk(requestId, requestOptions, 0);
  if(!firstData){
    return;
  }

  state.sessions = Array.isArray(firstData.sessions) ? firstData.sessions : [];
  state.sessionsTotalCount = getSessionListTotalCount(firstData, state.sessions.length);
  state.sessionRoot = firstData.root || '';
  state.hasLoadedSessions = true;
  state.isSessionsLoading = false;
  applyFilter();

  let offset = state.sessions.length;
  let hasMore = !!firstData.has_more && offset < state.sessionsTotalCount;
  state.isSessionsBackgroundLoading = hasMore;
  renderSessionList();

  while(hasMore){
    await new Promise(resolve => scheduleBackgroundTask(resolve, SESSION_BACKGROUND_APPEND_DELAY_MS));
    if(requestId !== loadSessionsRequestSeq){
      return;
    }

    const nextData = await fetchSessionListChunk(requestId, requestOptions, offset);
    if(!nextData){
      return;
    }

    const nextSessions = Array.isArray(nextData.sessions) ? nextData.sessions : [];
    if(nextSessions.length === 0){
      hasMore = false;
      break;
    }

    state.sessionRoot = nextData.root || state.sessionRoot;
    state.sessionsTotalCount = getSessionListTotalCount(nextData, state.sessions.length + nextSessions.length);
    appendLoadedSessions(nextSessions);
    offset += nextSessions.length;
    hasMore = !!nextData.has_more && offset < state.sessionsTotalCount;
    state.isSessionsBackgroundLoading = hasMore;
    renderSessionList();
  }
}

async function loadSessions(options){
  saveFilters();
  const requestId = ++loadSessionsRequestSeq;
  const loadMode = options && options.mode ? options.mode : 'auto';
  state.isSessionsLoading = true;
  state.isSessionsBackgroundLoading = false;
  state.sessionsError = '';
  state.sessionsLoadMode = loadMode;
  renderSessionList();
  const q = document.getElementById('q').value.trim();
  const searchMode = document.getElementById('mode').value;
  const sessionLabelId = getSelectedSessionLabelFilter();
  const eventLabelId = getSelectedListEventLabelFilter();
  const sortOrder = getActiveSortOrder();
  const params = buildSessionListRequestParams({
    q,
    searchMode,
    sessionLabelId,
    eventLabelId,
    sortOrder,
    force: loadMode === 'reload' || loadMode === 'reload_refresh',
  });
  if(shouldUseProgressiveInitialSessionLoad(loadMode)){
    state.sessions = [];
    state.filtered = [];
    state.sessionsTotalCount = 0;
    renderSessionList();
    try {
      await loadSessionsProgressively(requestId, {
        q,
        searchMode,
        sessionLabelId,
        eventLabelId,
        sortOrder,
      });
    } catch (error) {
      if(requestId !== loadSessionsRequestSeq){
        return;
      }
      state.sessionsError = normalizeRequestError(error, t('error.sessions'));
      renderSessionList();
    } finally {
      if(requestId === loadSessionsRequestSeq){
        state.isSessionsLoading = false;
        state.isSessionsBackgroundLoading = false;
        state.hasLoadedSessions = true;
        state.sessionsLoadMode = '';
        renderSessionList();
      }
    }
    return;
  }
  try {
    const data = await fetchJsonWithTimeout('/api/sessions?' + params.toString(), { cache: 'no-store' }, API_REQUEST_TIMEOUT_MS);
    if(requestId !== loadSessionsRequestSeq){
      return;
    }
    state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
    state.sessionsTotalCount = state.sessions.length;
    state.sessionsError = data.error || '';
    state.sessionRoot = data.root || '';
    applyFilter();
    if(state.activePath){
      const exists = state.sessions.some(s => s.path === state.activePath);
      if(exists){
        syncActiveSessionSummaryFromList(state.activePath);
        if(shouldSyncActiveSessionAfterListLoad(loadMode)){
          if(isAutomaticSessionsLoadMode(loadMode) && hasRecentDetailInteraction()){
            pendingAutomaticDetailSync = true;
            renderSessionList();
            renderActiveSession();
            scheduleDeferredAutomaticDetailSync();
          } else {
            pendingAutomaticDetailSync = false;
            clearDeferredDetailSyncTimer();
            await openSession(state.activePath, { mode: 'sync' });
          }
        } else {
          renderSessionList();
          renderActiveSession();
        }
      } else {
        state.activePath = null;
        state.activeSession = null;
        state.activeSessionVersion = '';
        state.activeExchangeRate = null;
        state.activeEvents = [];
        state.activeRawLineCount = 0;
        state.detailError = '';
        state.detailLoadMode = '';
        clearSelectedEventIds();
        clearMessageRangeSelection();
        pendingAutomaticDetailSync = false;
        clearDeferredDetailSyncTimer();
        renderSessionList();
        renderActiveSession();
      }
    }
  } catch (error) {
    if(requestId !== loadSessionsRequestSeq){
      return;
    }
    state.sessionsError = normalizeRequestError(error, t('error.sessions'));
    renderSessionList();
  } finally {
    if(requestId === loadSessionsRequestSeq){
      state.isSessionsLoading = false;
      state.hasLoadedSessions = true;
      state.sessionsLoadMode = '';
      renderSessionList();
      if(loadMode !== 'auto'){
        scheduleTodayUsageSummaryRefresh();
      }
    }
  }
}

function shouldReloadLabeledItems(){
  return state.leftPaneTab === 'labels' || state.hasLoadedLabeled;
}

async function loadLabeledItems(options){
  const requestId = ++loadLabeledItemsRequestSeq;
  const loadMode = options && options.mode ? options.mode : 'auto';
  state.isLabeledLoading = true;
  if(loadMode !== 'labels'){
    state.labeledError = '';
  }
  renderLabeledList();
  try {
    const data = await fetchJsonWithTimeout('/api/labeled-items?ts=' + Date.now(), { cache: 'no-store' }, API_REQUEST_TIMEOUT_MS);
    if(requestId !== loadLabeledItemsRequestSeq){
      return;
    }
    state.labeledSessions = Array.isArray(data.sessions) ? data.sessions : [];
    state.labeledEvents = Array.isArray(data.events) ? data.events : [];
    state.labeledError = data.error || '';
  } catch (error) {
    if(requestId !== loadLabeledItemsRequestSeq){
      return;
    }
    state.labeledError = normalizeRequestError(error, t('status.labels.errorTitle'));
  } finally {
    if(requestId === loadLabeledItemsRequestSeq){
      state.isLabeledLoading = false;
      state.hasLoadedLabeled = true;
      renderLabeledList();
    }
  }
}

function saveFilters(){
  const dateFromIso = parseDateInputToIso(getFpDateValue('date_from'));
  const dateToIso = parseDateInputToIso(getFpDateValue('date_to'));
  const eventDateFromDate = parseDateInputToIso(getFpDateValue('event_date_from_date'));
  const eventDateFromTime = parseTimeInputToValue(document.getElementById('event_date_from_time').value);
  const eventDateToDate = parseDateInputToIso(getFpDateValue('event_date_to_date'));
  const eventDateToTime = parseTimeInputToValue(document.getElementById('event_date_to_time').value);
  const detailEventDateFromDate = parseDateInputToIso(getFpDateValue('detail_event_date_from_date'));
  const detailEventDateFromTime = parseTimeInputToValue(document.getElementById('detail_event_date_from_time').value);
  const detailEventDateToDate = parseDateInputToIso(getFpDateValue('detail_event_date_to_date'));
  const detailEventDateToTime = parseTimeInputToValue(document.getElementById('detail_event_date_to_time').value);
  refreshDateTimeInputPairStates();
  const payload = {
    cwd_q: document.getElementById('cwd_q').value,
    date_from: dateFromIso,
    date_to: dateToIso,
    event_date_from_date: eventDateFromDate,
    event_date_from_time: eventDateFromTime,
    event_date_to_date: eventDateToDate,
    event_date_to_time: eventDateToTime,
    q: document.getElementById('q').value,
    mode: document.getElementById('mode').value,
    source_filter: document.getElementById('source_filter').value,
    subagents_filter: document.getElementById('subagents_filter').value,
    sort_order: getActiveSortOrder(),
    session_label_filter: getSelectedSessionLabelFilter(),
    event_label_filter: getSelectedListEventLabelFilter(),
    detail_event_label_filter: getSelectedDetailEventLabelFilter(),
    detail_cost_sort: getActiveDetailCostSortMode(),
    filters_visible: filtersVisible,
    detail_panel_section: normalizeDetailPanelSection(detailPanelSection),
    left_pane_visible: leftPaneVisible,
    left_pane_tab: state.leftPaneTab,
    panel_defaults_v: 3,
  };
  try {
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(payload));
  } catch (e) {
    // Ignore storage write errors.
  }
}

function restoreFilters(){
  let raw = null;
  try {
    raw = localStorage.getItem(FILTER_STORAGE_KEY);
  } catch (e) {
    raw = null;
  }
  if(!raw) return;
  try {
    const data = JSON.parse(raw);
    if(typeof data.cwd_q === 'string') document.getElementById('cwd_q').value = data.cwd_q;
    if(typeof data.date_from === 'string') setFpDateValue('date_from', parseDateInputToIso(data.date_from));
    if(typeof data.date_to === 'string') setFpDateValue('date_to', parseDateInputToIso(data.date_to));
    if(typeof data.event_date_from_date === 'string' || typeof data.event_date_from_time === 'string'){
      setFpDateTimeValue('event_date_from_date', 'event_date_from_time', parseDateInputToIso(data.event_date_from_date), parseTimeInputToValue(data.event_date_from_time));
    } else if(typeof data.event_date_from === 'string'){
      setDateTimePairFromIso('event_date_from_date', 'event_date_from_time', data.event_date_from);
    }
    if(typeof data.event_date_to_date === 'string' || typeof data.event_date_to_time === 'string'){
      setFpDateTimeValue('event_date_to_date', 'event_date_to_time', parseDateInputToIso(data.event_date_to_date), parseTimeInputToValue(data.event_date_to_time));
    } else if(typeof data.event_date_to === 'string'){
      setDateTimePairFromIso('event_date_to_date', 'event_date_to_time', data.event_date_to);
    }
    if(typeof data.q === 'string') document.getElementById('q').value = data.q;
    if(data.mode === 'and' || data.mode === 'or') document.getElementById('mode').value = data.mode;
    const source = normalizeSourceFilter(data.source_filter || 'all');
    document.getElementById('source_filter').value = source;
    if(data.subagents_filter === 'exclude') document.getElementById('subagents_filter').value = 'exclude';
    if(data.sort_order === 'asc' || data.sort_order === 'desc' || data.sort_order === 'updated') setActiveSortOrder(data.sort_order);
    if(typeof data.session_label_filter === 'string') document.getElementById('session_label_filter').dataset.pendingValue = data.session_label_filter;
    if(typeof data.event_label_filter === 'string') document.getElementById('event_label_filter').dataset.pendingValue = data.event_label_filter;
    if(typeof data.detail_event_label_filter === 'string') document.getElementById('detail_event_label_filter').dataset.pendingValue = data.detail_event_label_filter;
    if(data.detail_cost_sort === 'total' || data.detail_cost_sort === 'cost' || data.detail_cost_sort === 'score') document.getElementById('detail_cost_sort').value = data.detail_cost_sort;
    refreshDateTimeInputPairStates();
    if(data.panel_defaults_v >= 2){
      if(typeof data.filters_visible === 'boolean') filtersVisible = data.filters_visible;
      if(data.panel_defaults_v >= 3 && typeof data.detail_panel_section === 'string'){
        detailPanelSection = normalizeDetailPanelSection(data.detail_panel_section);
      } else if(typeof data.detail_actions_visible === 'boolean' && data.detail_actions_visible){
        detailPanelSection = 'actions';
      }
    }
    if(typeof data.left_pane_visible === 'boolean') leftPaneVisible = data.left_pane_visible;
    if(typeof data.left_pane_tab === 'string') state.leftPaneTab = normalizeLeftPaneTab(data.left_pane_tab);
  } catch (e) {
    // Ignore invalid saved filters.
  }
}

function clearFilters(){
  cancelScheduledSaveFilters();
  document.getElementById('cwd_q').value = '';
  clearFpInstance('date_from');
  clearFpInstance('date_to');
  clearFpInstance('event_date_from_date');
  clearFpInstance('event_date_from_time');
  clearFpInstance('event_date_to_date');
  clearFpInstance('event_date_to_time');
  document.getElementById('q').value = '';
  document.getElementById('mode').value = 'and';
  document.getElementById('source_filter').value = 'all';
  document.getElementById('subagents_filter').value = 'include';
  setActiveSortOrder('desc');
  document.getElementById('session_label_filter').value = '';
  document.getElementById('event_label_filter').value = '';
  document.getElementById('detail_event_label_filter').value = '';
  updateLabelSelectColor('session_label_filter');
  updateLabelSelectColor('event_label_filter');
  updateLabelSelectColor('detail_event_label_filter');
  refreshDateTimeInputPairStates();
  saveFilters();
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
    loadSessionsTimer = null;
  }
  loadSessions({ mode: 'clear' });
}

function getSessionListFilterState(){
  const fromRaw = getFpDateValue('date_from');
  const toRaw = getFpDateValue('date_to');
  const evFromRaw = buildDateTimeIsoFromParts(
    getFpDateValue('event_date_from_date'),
    document.getElementById('event_date_from_time').value,
    'start'
  );
  const evToRaw = buildDateTimeIsoFromParts(
    getFpDateValue('event_date_to_date'),
    document.getElementById('event_date_to_time').value,
    'end'
  );
  return {
    cwdQ: document.getElementById('cwd_q').value.toLowerCase().trim(),
    sourceFilter: normalizeSourceFilter(document.getElementById('source_filter').value || 'all'),
    subagentsFilter: normalizeSubagentsFilter(document.getElementById('subagents_filter').value || 'include'),
    fromTs: parseOptionalDateStart(fromRaw),
    toTs: parseOptionalDateEnd(toRaw),
    evFromTs: parseOptionalDatetimeStart(evFromRaw),
    evToTs: parseOptionalDatetimeEnd(evToRaw),
  };
}

function matchesSessionListFilters(session, filters){
  const cwdMatched = !filters.cwdQ || (session.cwd || '').toLowerCase().includes(filters.cwdQ);
  const sourceMatched = filters.sourceFilter === 'all' || normalizeSourceFilter(session.source_type || session.source) === filters.sourceFilter;
  const isSubagents = hasSubagentsSegment(session);
  const subagentsMatched = filters.subagentsFilter === 'exclude' ? !isSubagents : true;

  let dateMatched = true;
  if(filters.fromTs !== null || filters.toTs !== null){
    const sessionTs = toTimestamp(session.started_at || session.mtime);
    if(Number.isNaN(sessionTs)){
      dateMatched = false;
    } else {
      if(filters.fromTs !== null && sessionTs < filters.fromTs){
        dateMatched = false;
      }
      if(filters.toTs !== null && sessionTs > filters.toTs){
        dateMatched = false;
      }
    }
  }

  let eventDateMatched = true;
  if(filters.evFromTs !== null || filters.evToTs !== null){
    const minTs = session.min_event_ts ? toTimestamp(session.min_event_ts) : NaN;
    const maxTs = session.max_event_ts ? toTimestamp(session.max_event_ts) : NaN;
    if(Number.isNaN(minTs) || Number.isNaN(maxTs)){
      eventDateMatched = false;
    } else {
      if(filters.evFromTs !== null && maxTs < filters.evFromTs){
        eventDateMatched = false;
      }
      if(filters.evToTs !== null && minTs > filters.evToTs){
        eventDateMatched = false;
      }
    }
  }

  return cwdMatched && sourceMatched && subagentsMatched && dateMatched && eventDateMatched;
}

function applyFilter(){
  const filters = getSessionListFilterState();
  state.filtered = state.sessions.filter(session => matchesSessionListFilters(session, filters));
  saveFilters();
  renderSessionList();
}

function renderSessionCard(session, options){
  const opts = options || {};
  const className = opts.className || 'session-item';
  const isActive = !!opts.active;
  return `
      <div class="${className}${isActive ? ' active' : ''}" data-path="${esc(session.path)}">
        <div class="session-meta-row session-meta-row-secondary">
          <div class="session-badge session-cwd">${esc(session.cwd || '-')}</div>
        </div>
        <div class="session-meta-row session-meta-row-primary">
          <div class="session-badge session-time">${esc(fmt(session.started_at || session.mtime))}</div>
          <div class="session-badge session-source source-${esc(normalizeSource(session.source_type || session.source))}">${esc(sourceLabel(session.source_type || session.source))}</div>
          ${hasSubagentsSegment(session) ? '<div class="session-badge session-tag-subagents">subagents</div>' : ''}
        </div>
        <div class="session-preview">${esc(session.first_real_user_text || session.first_user_text || t('session.preview.empty'))}</div>
        ${(session.session_label_ids || session.session_labels || []).length ? `<div class="session-label-row">${renderAssignedLabels(session.session_labels && session.session_labels.length ? session.session_labels : resolveLabelsById(session.session_label_ids))}</div>` : ''}
      </div>
    `;
}

function getLabeledEventKindLabel(item){
  const kindKey = `labelsPane.kind.${item.kind || 'message'}`;
  const base = t(kindKey);
  if(item.kind === 'message' && item.role){
    return `${base} / ${item.role}`;
  }
  return base;
}

function getLabeledEventKindClass(item){
  if(item.kind === 'token_usage'){
    return 'kind-token';
  }
  if(item.kind === 'function_call' || item.kind === 'function_output'){
    return 'kind-tool';
  }
  return 'kind-message';
}

function renderLabeledSessionCard(session){
  return `
    <div class="labeled-item ${state.activePath === session.path ? 'active' : ''}" data-item-kind="session" data-path="${esc(session.path)}">
      <div class="labeled-item-row">
        <span class="labeled-item-badge type-session">${esc(t('labelsPane.item.session'))}</span>
        <div class="session-badge session-time">${esc(fmt(session.started_at || session.mtime))}</div>
        <div class="session-badge session-source source-${esc(normalizeSource(session.source_type || session.source))}">${esc(sourceLabel(session.source_type || session.source))}</div>
        ${hasSubagentsSegment(session) ? '<div class="session-badge session-tag-subagents">subagents</div>' : ''}
      </div>
      <div class="labeled-item-row">
        <div class="session-badge session-cwd">${esc(session.cwd || '-')}</div>
      </div>
      <div class="labeled-item-row">
        <div class="labeled-item-preview">${esc(session.first_real_user_text || session.first_user_text || t('session.preview.empty'))}</div>
      </div>
      <div class="labeled-item-row session-label-row">${renderAssignedLabels(session.session_labels && session.session_labels.length ? session.session_labels : resolveLabelsById(session.session_label_ids))}</div>
      <div class="labeled-item-row">
        <div class="labeled-item-path">${highlightSessionPath(session.relative_path)}</div>
      </div>
    </div>
  `;
}

function renderLabeledEventCard(item){
  const isActive = state.activePath === item.path && pendingLabeledEventFocusId === item.event_id;
  return `
    <div class="labeled-item ${isActive ? 'active' : ''}" data-item-kind="event" data-path="${esc(item.path)}" data-event-id="${esc(item.event_id)}">
      <div class="labeled-item-row">
        <span class="labeled-item-badge type-event">${esc(t('labelsPane.item.event'))}</span>
        <span class="labeled-item-badge ${esc(getLabeledEventKindClass(item))}">${esc(getLabeledEventKindLabel(item))}</span>
        <div class="session-badge session-time">${esc(fmt(item.timestamp || item.session_started_at || item.session_mtime))}</div>
        <div class="session-badge session-source source-${esc(normalizeSource(item.source))}">${esc(sourceLabel(item.source))}</div>
      </div>
      <div class="labeled-item-row">
        <div class="session-badge session-cwd">${esc(item.cwd || '-')}</div>
      </div>
      <div class="labeled-item-row">
        <div class="labeled-item-preview">${esc(item.preview || t('session.preview.empty'))}</div>
      </div>
      <div class="labeled-item-row session-label-row">${renderAssignedLabels(item.labels || [])}</div>
      <div class="labeled-item-row">
        <div class="labeled-item-path">${highlightSessionPath(item.relative_path)}</div>
      </div>
    </div>
  `;
}

function renderLabeledGroup(title, count, cardsHtml){
  return `
    <section class="labeled-group">
      <div class="labeled-group-head">
        <div class="labeled-group-title">${esc(title)}</div>
        <div class="labeled-group-count">${esc(String(count))}</div>
      </div>
      ${cardsHtml}
    </section>
  `;
}

function renderSessionList(options){
  const opts = options || {};
  const appendSessions = Array.isArray(opts.appendSessions) ? opts.appendSessions : null;
  const previousFilteredCount = Number.isFinite(opts.previousFilteredCount) ? opts.previousFilteredCount : 0;
  const box = document.getElementById('sessions');
  const previousScrollTop = box ? box.scrollTop : 0;
  updateReloadButtonState();
  if(state.isSessionsLoading && !state.hasLoadedSessions){
    box.innerHTML = renderInlineStatus(
      t('status.sessions.loadingTitle'),
      t('status.sessions.loadingCopy'),
      'loading'
    );
  } else if(state.sessionsError && !state.sessions.length){
    box.innerHTML = renderInlineStatus(
      t('status.sessions.errorTitle'),
      state.sessionsError,
      'error'
    );
  } else if(!state.filtered.length){
    box.innerHTML = hasListFilter()
      ? renderInlineStatus(
          t('status.sessions.noMatchesTitle'),
          t('status.sessions.noMatchesCopy'),
          'empty'
        )
      : renderInlineStatus(
          t('status.sessions.emptyTitle'),
          t('status.sessions.emptyCopy'),
          'empty'
        );
  } else if(appendSessions && (appendSessions.length > 0 || previousFilteredCount > 0)){
    if(previousFilteredCount === 0){
      box.innerHTML = appendSessions.map(s => renderSessionCard(s, { active: state.activePath === s.path })).join('');
    } else if(appendSessions.length > 0){
      box.insertAdjacentHTML('beforeend', appendSessions.map(s => renderSessionCard(s, { active: state.activePath === s.path })).join(''));
    }
  } else {
    box.innerHTML = state.filtered.map(s => renderSessionCard(s, { active: state.activePath === s.path })).join('');
  }
  if(box){
    box.scrollTop = previousScrollTop;
  }
  updateSessionsBackgroundIndicator();
  if(state.isSessionsLoading && state.hasLoadedSessions && (state.sessionsLoadMode === 'reload' || state.sessionsLoadMode === 'reload_refresh' || state.sessionsLoadMode === 'auto' || state.sessionsLoadMode === 'clear')){
    setStatusLayer(
      'sessions_status',
      t('status.sessions.refreshTitle'),
      t('status.sessions.refreshCopy'),
      'loading'
    );
  } else {
    setStatusLayer('sessions_status');
  }
  const countEl = document.getElementById('session_count');
  if(countEl){
    if(state.hasLoadedSessions && (state.sessions.length > 0 || state.sessionsTotalCount > 0)){
      const currentIndex = state.activePath ? state.filtered.findIndex(s => s.path === state.activePath) : -1;
      const currentLabel = currentIndex >= 0 ? String(currentIndex + 1) : '-';
      const totalCount = state.sessionsTotalCount || state.sessions.length;
      countEl.textContent = t('summary.sessions', { current: currentLabel, filtered: state.filtered.length, total: totalCount });
    } else {
      countEl.textContent = '';
    }
  }
}

function renderLabeledList(){
  const box = document.getElementById('labeled_items');
  const countEl = document.getElementById('labeled_count');
  if(!box){
    return;
  }

  const sessionCount = state.labeledSessions.length;
  const eventCount = state.labeledEvents.length;
  if(state.isLabeledLoading && !state.hasLoadedLabeled){
    box.innerHTML = renderInlineStatus(
      t('status.labels.loadingTitle'),
      t('status.labels.loadingCopy'),
      'loading'
    );
  } else if(state.labeledError && sessionCount === 0 && eventCount === 0){
    box.innerHTML = renderInlineStatus(
      t('status.labels.errorTitle'),
      state.labeledError,
      'error'
    );
  } else if(sessionCount === 0 && eventCount === 0){
    box.innerHTML = renderInlineStatus(
      t('status.labels.emptyTitle'),
      t('status.labels.emptyCopy'),
      'empty'
    );
  } else {
    const groups = [];
    if(sessionCount > 0){
      groups.push(renderLabeledGroup(
        t('labelsPane.group.sessions'),
        sessionCount,
        state.labeledSessions.map(renderLabeledSessionCard).join('')
      ));
    }
    if(eventCount > 0){
      groups.push(renderLabeledGroup(
        t('labelsPane.group.events'),
        eventCount,
        state.labeledEvents.map(renderLabeledEventCard).join('')
      ));
    }
    box.innerHTML = groups.join('');
  }

  if(countEl){
    countEl.textContent = state.hasLoadedLabeled
      ? t('summary.labels', { sessions: sessionCount, events: eventCount })
      : '';
  }

  if(state.isLabeledLoading && state.hasLoadedLabeled){
    setStatusLayer(
      'labeled_items_status',
      t('status.labels.loadingTitle'),
      t('status.labels.loadingCopy'),
      'loading'
    );
  } else {
    setStatusLayer('labeled_items_status');
  }
}

function applyDetailMessageDisplayFilters(events){
  let filteredEvents = Array.isArray(events) ? events : [];
  const showOnlyTokenUsage = !!document.getElementById('only_token_usage').checked;
  const showOnlyUser = document.getElementById('only_user_instruction').checked;
  const showOnlyAssistant = document.getElementById('only_ai_response').checked;
  const showTurnBoundaryOnly = isTurnBoundaryFilterEnabled();
  const hasMessageDisplayFilter = showTurnBoundaryOnly || showOnlyUser || showOnlyAssistant;
  if(!showOnlyTokenUsage && !hasMessageDisplayFilter){
    return filteredEvents;
  }
  const messageSource = showTurnBoundaryOnly ? filterEventsToTurnBoundaries(filteredEvents) : filteredEvents;
  const visibleMessageEvents = new Set();
  if(hasMessageDisplayFilter){
    messageSource.forEach(ev => {
      if(ev.kind !== 'message'){
        return;
      }
      if(!showOnlyUser && !showOnlyAssistant){
        visibleMessageEvents.add(ev);
        return;
      }
      if(showOnlyUser && ev.role === 'user'){
        if(isSystemLabeledUserEvent(ev)){
          return;
        }
        visibleMessageEvents.add(ev);
        return;
      }
      if(showOnlyAssistant && ev.role === 'assistant'){
        visibleMessageEvents.add(ev);
      }
    });
  }
  filteredEvents = filteredEvents.filter(ev => {
    if(showOnlyTokenUsage && ev.kind === 'token_usage'){
      return true;
    }
    return visibleMessageEvents.has(ev);
  });
  return filteredEvents;
}

function applyDetailMessageRangeFilter(events, rawIndexByEvent){
  if(!state.detailMessageRangeMode){
    return events;
  }
  const selectedMessage = getSelectedMessageRangeEvent();
  if(!selectedMessage){
    return events;
  }
  const activeEvents = state.activeEvents || [];
  const selectedIndex = activeEvents.findIndex(ev => ev === selectedMessage);
  if(selectedIndex < 0){
    return events;
  }
  return events.filter(ev => {
    const rawIndex = rawIndexByEvent.get(ev);
    if(typeof rawIndex !== 'number'){
      return false;
    }
    if(state.detailMessageRangeMode === 'after'){
      return rawIndex >= selectedIndex;
    }
    if(state.detailMessageRangeMode === 'before'){
      return rawIndex <= selectedIndex;
    }
    return true;
  });
}

function applySelectedEventsOnlyFilter(events){
  if(!isSelectedEventsOnlyFilterEnabled()){
    return events;
  }
  const selectedIds = state.selectedEventIds || new Set();
  if(!selectedIds.size){
    return [];
  }
  return events.filter(ev => {
    const key = getEventSelectionKey(ev);
    return !!key && selectedIds.has(key);
  });
}

function filterEventsByDetailKeywordTerm(events){
  if(detailKeywordFilterTerm === ''){
    return events;
  }
  return events.filter(ev => containsLiteralKeyword(getEventBodyText(ev), detailKeywordFilterTerm));
}

function applyDetailEventDateFilter(events){
  const detailEvFromRaw = buildDateTimeIsoFromParts(
    getFpDateValue('detail_event_date_from_date'),
    document.getElementById('detail_event_date_from_time').value,
    'start'
  );
  const detailEvToRaw = buildDateTimeIsoFromParts(
    getFpDateValue('detail_event_date_to_date'),
    document.getElementById('detail_event_date_to_time').value,
    'end'
  );
  const detailEvFromTs = parseOptionalDatetimeStart(detailEvFromRaw);
  const detailEvToTs = parseOptionalDatetimeEnd(detailEvToRaw);
  if(detailEvFromTs === null && detailEvToTs === null){
    return events;
  }
  return events.filter(ev => {
    const evTs = ev.timestamp ? toTimestamp(ev.timestamp) : NaN;
    if(Number.isNaN(evTs)) return false;
    if(detailEvFromTs !== null && evTs < detailEvFromTs) return false;
    if(detailEvToTs !== null && evTs > detailEvToTs) return false;
    return true;
  });
}

function applyDetailVisibilityFilters(events, rawIndexByEvent){
  let filteredEvents = Array.isArray(events) ? events : [];
  const selectedEventLabelId = getSelectedDetailEventLabelFilter();
  if(selectedEventLabelId){
    filteredEvents = filteredEvents.filter(ev => (ev.labels || []).some(label => String(label.id) === selectedEventLabelId));
  }
  filteredEvents = applySelectedEventsOnlyFilter(filteredEvents);
  filteredEvents = applyDetailMessageDisplayFilters(filteredEvents);
  filteredEvents = applyDetailMessageRangeFilter(filteredEvents, rawIndexByEvent);
  filteredEvents = filterEventsByDetailKeywordTerm(filteredEvents);
  filteredEvents = applyDetailEventDateFilter(filteredEvents);
  return filteredEvents;
}

function buildTokenUsageSortGroups(events){
  const preludeEvents = [];
  const groups = [];
  let currentGroup = null;

  function flushGroup(){
    if(!currentGroup){
      return;
    }
    groups.push({
      originalIndex: groups.length,
      events: currentGroup.events,
      lastTokenUsage: currentGroup.lastTokenUsage,
    });
    currentGroup = null;
  }

  (Array.isArray(events) ? events : []).forEach(ev => {
    const isTurnStart = ev && ev.kind === 'message' && ev.role === 'user' && !isSystemLabeledUserEvent(ev);
    if(isTurnStart){
      flushGroup();
      currentGroup = {
        events: [ev],
        lastTokenUsage: null,
      };
      return;
    }
    if(currentGroup){
      currentGroup.events.push(ev);
      if(ev && ev.kind === 'token_usage'){
        currentGroup.lastTokenUsage = ev;
      }
      return;
    }
    preludeEvents.push(ev);
  });

  flushGroup();
  return { preludeEvents, groups };
}

function getTokenUsageSortMetric(group, mode){
  if(!group || !group.lastTokenUsage || !group.lastTokenUsage.usage){
    return null;
  }
  const usage = group.lastTokenUsage.usage;
  if(mode === 'total'){
    const totalTokens = getUsageTotalTokens(usage);
    return totalTokens > 0 ? totalTokens : null;
  }
  if(mode === 'cost'){
    const costUsd = Number(usage.cost_usd);
    return Number.isFinite(costUsd) ? costUsd : null;
  }
  if(mode === 'score'){
    const performance = getUsageCostPerformance(getUsageTotalTokens(usage), usage.cost_usd);
    return typeof performance.score === 'number' ? performance.score : null;
  }
  return null;
}

function sortTokenUsageGroups(groups, mode){
  return [...groups].sort((left, right) => {
    const leftMetric = getTokenUsageSortMetric(left, mode);
    const rightMetric = getTokenUsageSortMetric(right, mode);
    const leftHasMetric = leftMetric !== null;
    const rightHasMetric = rightMetric !== null;
    if(leftHasMetric && rightHasMetric){
      if(leftMetric < rightMetric){
        return 1;
      }
      if(leftMetric > rightMetric){
        return -1;
      }
    } else if(leftHasMetric !== rightHasMetric){
      return leftHasMetric ? -1 : 1;
    }
    return left.originalIndex - right.originalIndex;
  });
}

function getDisplayEvents(){
  const rawEvents = state.activeEvents || [];
  const rawIndexByEvent = new Map(rawEvents.map((ev, index) => [ev, index]));
  const reverseOrder = document.getElementById('reverse_order').checked;
  const detailCostSortMode = getActiveDetailCostSortMode();
  if(!detailCostSortMode){
    let events = applyDetailVisibilityFilters(rawEvents, rawIndexByEvent);
    if(reverseOrder){
      events = [...events].reverse();
    }
    return events;
  }
  const groupedEvents = buildTokenUsageSortGroups(rawEvents);
  if(groupedEvents.groups.length === 0){
    let events = applyDetailVisibilityFilters(rawEvents, rawIndexByEvent);
    if(reverseOrder){
      events = [...events].reverse();
    }
    return events;
  }
  const flattenedEvents = [];
  let preludeEvents = applyDetailVisibilityFilters(groupedEvents.preludeEvents, rawIndexByEvent);
  if(reverseOrder){
    preludeEvents = [...preludeEvents].reverse();
  }
  flattenedEvents.push(...preludeEvents);
  sortTokenUsageGroups(groupedEvents.groups, detailCostSortMode).forEach(group => {
    let visibleGroupEvents = applyDetailVisibilityFilters(group.events, rawIndexByEvent);
    if(!visibleGroupEvents.length){
      return;
    }
    if(reverseOrder){
      visibleGroupEvents = [...visibleGroupEvents].reverse();
    }
    flattenedEvents.push(...visibleGroupEvents);
  });
  return flattenedEvents;
}

function formatCopiedMessages(events){
  return events.map(ev => {
    const label = ev.kind === 'message'
      ? (ev.role || 'system')
      : (ev.kind || 'event');
    const timestamp = fmt(ev.timestamp) || ev.timestamp || '-';
    return `[${label}] ${timestamp}
${getCopyableEventText(ev)}`;
  }).join('\n\n-----\n\n');
}

async function removeSessionLabel(labelId){
  if(!state.activePath) return;
  const data = await postJson('/api/session-label/remove', {
    path: state.activePath,
    label_id: labelId,
  });
  if(data.error){
    alert(data.error);
    return;
  }
  await refreshLabeledViews();
}

async function addSessionLabelFromButton(button){
  if(!state.activePath) return;
  showLabelPicker(button, async (labelId) => {
    const data = await postJson('/api/session-label/add', {
      path: state.activePath,
      label_id: labelId,
    });
    if(data.error){
      alert(data.error);
      return;
    }
    await refreshLabeledViews();
  });
}

async function addEventLabelFromButton(button, eventId){
  if(!state.activePath || !eventId) return;
  showLabelPicker(button, async (labelId) => {
    const data = await postJson('/api/event-label/add', {
      path: state.activePath,
      event_id: eventId,
      label_id: labelId,
    });
    if(data.error){
      alert(data.error);
      return;
    }
    await refreshLabeledViews();
  });
}

async function removeEventLabel(eventId, labelId){
  if(!state.activePath || !eventId) return;
  const data = await postJson('/api/event-label/remove', {
    path: state.activePath,
    event_id: eventId,
    label_id: labelId,
  });
  if(data.error){
    alert(data.error);
    return;
  }
  await refreshLabeledViews();
}

async function refreshLabeledViews(){
  await loadSessions({ mode: 'labels' });
  if(shouldReloadLabeledItems()){
    await loadLabeledItems({ mode: 'labels' });
  }
}

async function copyDisplayedMessages(){
  const messages = getDisplayCopyableEvents();
  if(!messages.length){
    return;
  }
  const copied = await copyTextToClipboard(formatCopiedMessages(messages));
  if(copied){
    const button = document.getElementById('copy_displayed_messages');
    flashButtonLabel(button, t('copy.displayedCount', { count: messages.length }), t('detail.copyDisplayed'));
  }
}

async function copySelectedMessages(){
  const messages = getSelectedMessageEvents();
  if(!messages.length){
    return;
  }
  const copied = await copyTextToClipboard(formatCopiedMessages(messages));
  if(copied){
    const copiedCount = messages.length;
    const button = document.getElementById('copy_selected_messages');
    flashButtonLabel(button, t('copy.selectedCount', { count: copiedCount }), t('detail.copySelected'), BUTTON_FEEDBACK_MS);
    await waitForUiFeedback(BUTTON_FEEDBACK_MS);
    state.isEventSelectionMode = false;
    clearSelectedEventIds();
    renderActiveSession();
  }
}

async function copyEventMessage(button, eventId){
  const event = (state.activeEvents || []).find(ev => ev.event_id === eventId);
  const text = getCopyableEventText(event);
  if(!text){
    return;
  }
  const copied = await copyTextToClipboard(text);
  if(copied){
    flashButtonLabel(button, t('copy.copied'), t('copy.single'));
  }
}

function toggleEventSelectionMode(){
  const nextEnabled = !state.isEventSelectionMode;
  state.isEventSelectionMode = nextEnabled;
  if(nextEnabled){
    state.isMessageRangeSelectionMode = false;
    setActiveDetailPanelSection('range', { skipSave: true });
  } else {
    clearSelectedEventIds();
  }
  renderActiveSession();
}

function updateEventSelection(eventId, checked, card){
  const key = String(eventId || '');
  if(!key){
    return;
  }
  if(checked){
    state.selectedEventIds.add(key);
  } else {
    state.selectedEventIds.delete(key);
  }
  if(card){
    card.classList.toggle('copy-selected', checked);
  }
  if(isSelectedEventsOnlyFilterEnabled() || state.selectedEventIds.size === 0){
    const eventsBox = document.getElementById('events');
    pendingEventsScrollRestoreTop = eventsBox ? eventsBox.scrollTop : null;
    renderActiveSession();
    return;
  }
  updateCopySelectedMessagesButtonState();
  updateSelectedEventsOnlyToggleState();
  updateClearDetailButtonState();
}

function toggleMessageRangeSelectionMode(){
  const nextEnabled = !state.isMessageRangeSelectionMode;
  state.isMessageRangeSelectionMode = nextEnabled;
  if(nextEnabled){
    state.isEventSelectionMode = false;
    clearSelectedEventIds();
    setActiveDetailPanelSection('range', { skipSave: true });
  }
  renderActiveSession();
}

function updateMessageRangeSelection(eventId){
  const key = String(eventId || '');
  if(!key){
    return;
  }
  const eventsBox = document.getElementById('events');
  pendingEventsScrollRestoreTop = eventsBox ? eventsBox.scrollTop : null;
  noteDetailInteraction();
  state.selectedMessageRangeEventId = key;
  renderActiveSession();
}

function applyDetailMessageRange(mode){
  if(!getSelectedMessageRangeEvent()){
    return;
  }
  noteDetailInteraction();
  state.detailMessageRangeMode = mode === 'before' ? 'before' : 'after';
  const eventsBox = document.getElementById('events');
  if(eventsBox){
    eventsBox.scrollTop = 0;
  }
  renderActiveSession();
}

function clearDetailMessageRangeSelection(){
  noteDetailInteraction();
  clearMessageRangeSelection();
  renderActiveSession();
}

function applyDetailKeywordFilter(){
  noteDetailInteraction();
  const inputValue = getDetailKeywordInputValue();
  if(detailKeywordFilterTerm !== '' && inputValue === detailKeywordFilterTerm){
    detailKeywordFilterTerm = '';
  } else {
    detailKeywordFilterTerm = inputValue;
  }
  const eventsBox = document.getElementById('events');
  if(eventsBox){
    eventsBox.scrollTop = 0;
  }
  renderActiveSession();
}

function runDetailKeywordSearch(){
  noteDetailInteraction();
  detailKeywordSearchTerm = getDetailKeywordInputValue();
  const searchMeta = buildDetailKeywordSearchMeta(getDisplayEvents(), detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  detailKeywordCurrentMatchIndex = searchMeta.total ? 0 : -1;
  pendingDetailKeywordFocusIndex = detailKeywordCurrentMatchIndex;
  renderActiveSession();
}

function moveDetailKeywordSearch(step){
  noteDetailInteraction();
  const searchMeta = buildDetailKeywordSearchMeta(getDisplayEvents(), detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  if(!searchMeta.total){
    detailKeywordCurrentMatchIndex = -1;
    pendingDetailKeywordFocusIndex = -1;
    renderActiveSession();
    return;
  }
  if(detailKeywordCurrentMatchIndex < 0 || detailKeywordCurrentMatchIndex >= searchMeta.total){
    detailKeywordCurrentMatchIndex = 0;
  } else {
    detailKeywordCurrentMatchIndex = (detailKeywordCurrentMatchIndex + step + searchMeta.total) % searchMeta.total;
  }
  pendingDetailKeywordFocusIndex = detailKeywordCurrentMatchIndex;
  renderActiveSession();
}

function clearDetailKeyword(){
  noteDetailInteraction();
  const input = document.getElementById('detail_keyword_q');
  if(input){
    input.value = '';
  }
  resetDetailKeywordState();
  renderActiveSession();
}

function clearDetailFilters(){
  noteDetailInteraction();
  document.getElementById('only_user_instruction').checked = false;
  document.getElementById('only_ai_response').checked = false;
  document.getElementById('turn_boundary_only').checked = false;
  document.getElementById('only_token_usage').checked = false;
  document.getElementById('selected_events_only').checked = false;
  document.getElementById('reverse_order').checked = false;
  document.getElementById('detail_cost_sort').value = '';
  const detailEventLabelFilter = document.getElementById('detail_event_label_filter');
  detailEventLabelFilter.value = '';
  delete detailEventLabelFilter.dataset.pendingValue;
  const detailKeywordInput = document.getElementById('detail_keyword_q');
  if(detailKeywordInput){
    detailKeywordInput.value = '';
  }
  clearFpInstance('detail_event_date_from_date');
  document.getElementById('detail_event_date_from_time').value = '';
  clearFpInstance('detail_event_date_to_date');
  document.getElementById('detail_event_date_to_time').value = '';
  refreshDateTimeInputPairStates();
  resetDetailKeywordState();
  state.isEventSelectionMode = false;
  clearSelectedEventIds();
  clearMessageRangeSelection();
  hideLabelPicker();
  saveFilters();
  renderActiveSession();
}

function renderActiveSession(){
  const meta = document.getElementById('meta');
  const eventsBox = document.getElementById('events');
  updateRefreshDetailButtonState();
  updateDetailDisplayControlsState();
  const sessionRootRow = state.sessionRoot
    ? `<div class="header-meta-row">
      <span class="header-meta-label">${esc(t('meta.sessionRoot'))}</span>
      <span class="header-meta-value">${esc(state.sessionRoot)}</span>
    </div>`
    : '';
  if(!state.activeSession){
    detailKeywordSearchTotal = 0;
    normalizeDetailKeywordSearchPosition({ total: 0 });
    if(state.isDetailLoading && state.activePath){
      meta.innerHTML = `${sessionRootRow}<div class="header-meta-row"><span class="header-meta-text">${esc(t('status.detail.loadingTitle'))}</span></div>`;
      eventsBox.innerHTML = renderInlineStatus(
        t('status.detail.loadingTitle'),
        t('status.detail.loadingCopy'),
        'loading'
      );
    } else if(state.detailError){
      meta.innerHTML = `${sessionRootRow}<div class="header-meta-row"><span class="header-meta-text error">${esc(state.detailError)}</span></div>`;
      eventsBox.innerHTML = renderInlineStatus(
        t('status.detail.errorTitle'),
        state.detailError,
        'error'
      );
    } else {
      meta.innerHTML = `${sessionRootRow}<div class="header-meta-row"><span class="header-meta-text">${esc(t('status.detail.selectSession'))}</span></div>`;
      eventsBox.innerHTML = '';
    }
    updateDetailMetaVisibility();
    setStatusLayer('detail_status');
    updateCopyResumeButtonState();
    updateDisplayedMessagesCopyButtonState();
    updateEventSelectionModeButtonState();
    updateSelectedEventsOnlyToggleState();
    updateCopySelectedMessagesButtonState();
    updateMessageRangeSelectionModeButtonState();
    updateClearMessageRangeSelectionButtonState();
    updateMessageRangeFilterButtonsState();
    updateDetailKeywordControls({ total: 0 });
    renderSessionLabelStrip();
    updateSessionLabelButtonState();
    return;
  }

  syncSelectedEventIdsToActiveEvents();
  syncSelectedMessageRangeToActiveEvents();
  const displayEvents = getDisplayEvents();
  const searchMeta = buildDetailKeywordSearchMeta(displayEvents, detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  normalizeDetailKeywordSearchPosition(searchMeta);
  const sourceType = state.activeSession.source_type || state.activeSession.source || '';
  const source = normalizeSource(sourceType);
  const eventsSummary = state.isDetailLoading && state.activeEvents.length === 0
    ? t('summary.eventsLoading')
    : t('summary.events', { visible: displayEvents.length, total: state.activeEvents.length });
  const rawSummary = t('summary.raw', {
    count: state.isDetailLoading && state.activeEvents.length === 0 ? '...' : state.activeRawLineCount,
  });
  const usageMetaRows = renderUsageMetaRows(state.activeSession.usage);
  const modelMetaRow = renderModelMetaRow(state.activeSession);
  const errorNote = state.detailError
    ? `<span class="header-meta-text error">${esc(t('meta.status'))}: ${esc(state.detailError)}</span>`
    : '';
  meta.innerHTML = `
    ${sessionRootRow}
    <div class="header-meta-row">
      <span class="header-meta-label">${esc(t('meta.path'))}</span>
      <span class="header-meta-value">${highlightSessionPath(state.activeSession.relative_path)}</span>
    </div>
    <div class="header-meta-row">
      <span class="header-meta-label">${esc(t('meta.cwd'))}</span>
      <span class="header-meta-value">${esc(state.activeSession.cwd || '-')}</span>
    </div>
    <div class="header-meta-row">
      <span class="header-meta-label">${esc(t('meta.time'))}</span>
      <span class="header-meta-value">${esc(fmt(state.activeSession.started_at || state.activeSession.mtime))}</span>
      <span class="meta-tag source-${esc(source)}">${esc(sourceLabel(sourceType))}</span>
      <span class="header-meta-text">${esc(eventsSummary)}</span>
      <span class="header-meta-text">${esc(rawSummary)}</span>
      ${errorNote}
    </div>
    ${usageMetaRows}
    ${modelMetaRow}`;
  updateDetailMetaVisibility();

  if(state.isDetailLoading && state.activeEvents.length === 0){
    eventsBox.innerHTML = renderInlineStatus(
      t('status.detail.loadingTitle'),
      t('status.detail.loadingCopy'),
      'loading'
    );
  } else if(state.detailError && state.activeEvents.length === 0){
    eventsBox.innerHTML = renderInlineStatus(
      t('status.detail.errorTitle'),
      state.detailError,
      'error'
    );
  } else if(displayEvents.length === 0){
    eventsBox.innerHTML = state.activeEvents.length === 0
      ? renderInlineStatus(
          t('status.detail.noDisplayTitle'),
          t('status.detail.noDisplayCopy'),
          'empty'
        )
      : renderInlineStatus(
          t('status.detail.noMatchTitle'),
          t('status.detail.noMatchCopy'),
          'empty'
        );
  } else {
    renderEventList(eventsBox, displayEvents, getSelectedDetailEventLabelFilter(), searchMeta);
  }
  if(state.isDetailLoading && state.activeEvents.length > 0 && state.detailLoadMode === 'refresh'){
    setStatusLayer(
      'detail_status',
      t('status.detail.refreshTitle'),
      t('status.detail.refreshCopy'),
      'loading'
    );
  } else {
    setStatusLayer('detail_status');
  }
  renderSessionLabelStrip();
  updateSessionLabelButtonState();
  updateDisplayedMessagesCopyButtonState();
  updateEventSelectionModeButtonState();
  updateCopySelectedMessagesButtonState();
  updateMessageRangeSelectionModeButtonState();
  updateClearMessageRangeSelectionButtonState();
  updateMessageRangeFilterButtonsState();
  updateDetailKeywordControls(searchMeta);
  updateCopyResumeButtonState();
}

function safeRenderActiveSession(){
  try {
    renderActiveSession();
  } catch (error) {
    state.isDetailLoading = false;
    state.detailLoadMode = '';
    state.detailError = normalizeRequestError(error, t('error.detail'));
    const meta = document.getElementById('meta');
    const eventsBox = document.getElementById('events');
    if(meta){
      meta.innerHTML = `<div class="header-meta-row"><span class="header-meta-text error">${esc(state.detailError)}</span></div>`;
    }
    if(eventsBox){
      eventsBox.innerHTML = renderInlineStatus(
        t('status.detail.errorTitle'),
        state.detailError,
        'error'
      );
    }
    setStatusLayer('detail_status');
  }
}

async function openSession(path, options){
  const requestId = ++loadSessionDetailRequestSeq;
  const nextSession = state.sessions.find(s => s.path === path) || null;
  const previousPath = state.activeSession && state.activeSession.path ? state.activeSession.path : state.activePath;
  const loadMode = options && options.mode ? options.mode : 'open';
  const canCheckVersionFirst = previousPath === path
    && state.activeEvents.length > 0
    && !!state.activeSessionVersion
    && (loadMode === 'open' || loadMode === 'refresh' || loadMode === 'sync');
  if(!(options && options.preserveLabeledFocus)){
    pendingLabeledEventFocusId = '';
  }
  if(loadMode !== 'sync'){
    pendingAutomaticDetailSync = false;
    clearDeferredDetailSyncTimer();
  }
  if(nextSession){
    state.activeSession = {
      ...(state.activeSession || {}),
      ...nextSession,
    };
  }
  state.activePath = path;
  if(!state.activeSession || state.activeSession.path !== path){
    state.activeSession = nextSession;
  }
  if(canCheckVersionFirst){
    renderSessionList();
    renderLabeledList();
    safeRenderActiveSession();
    try {
      const versionData = await fetchSessionVersion(path);
      if(requestId !== loadSessionDetailRequestSeq){
        return;
      }
      if(!versionData.error && versionData.session_version && versionData.session_version === state.activeSessionVersion){
        state.detailError = '';
        renderSessionList();
        safeRenderActiveSession();
        return;
      }
    } catch (error) {
      if(requestId !== loadSessionDetailRequestSeq){
        return;
      }
      // Fall through to the full refresh path when version check fails.
    }
  }
  const effectiveLoadMode = canCheckVersionFirst ? 'refresh' : loadMode;
  state.isDetailLoading = true;
  state.detailError = '';
  state.detailLoadMode = effectiveLoadMode;
  state.activeExchangeRate = null;
  if(previousPath !== path){
    state.activeEvents = [];
    state.activeRawLineCount = 0;
    state.activeSessionVersion = '';
    clearSelectedEventIds();
    clearMessageRangeSelection();
  }
  renderSessionList();
  renderLabeledList();
  safeRenderActiveSession();
  scheduleDetailLoadingWatchdog(path, requestId);
  try {
    const metaUrl = '/api/session?path=' + encodeURIComponent(path) + '&include_events=false&ts=' + Date.now();
    const metaData = await fetchJsonWithTimeout(metaUrl, { cache: 'no-store' }, API_REQUEST_TIMEOUT_MS);
    if(requestId !== loadSessionDetailRequestSeq) return;
    if(metaData.error){
      state.detailError = metaData.error;
      if(!state.activeEvents.length) state.activeRawLineCount = 0;
      return;
    }
    state.activeSession = metaData.session || nextSession;
    state.activeSessionVersion = metaData.session_version || state.activeSessionVersion;
    state.activeExchangeRate = metaData.exchange_rate || null;
    state.detailError = '';
    safeRenderActiveSession();

    const eventsUrl = '/api/session?path=' + encodeURIComponent(path) + '&ts=' + Date.now();
    const eventsData = await fetchJsonWithTimeout(eventsUrl, { cache: 'no-store' }, API_REQUEST_TIMEOUT_MS);
    if(requestId !== loadSessionDetailRequestSeq) return;
    if(eventsData.error){
      state.detailError = eventsData.error;
      if(!state.activeEvents.length) state.activeRawLineCount = 0;
      return;
    }
    state.activeSession = eventsData.session || state.activeSession;
    state.activeSessionVersion = eventsData.session_version || state.activeSessionVersion;
    state.activeExchangeRate = eventsData.exchange_rate || state.activeExchangeRate;
    state.activeEvents = eventsData.events || [];
    state.activeRawLineCount = eventsData.raw_line_count || 0;
    state.detailError = '';
    syncSelectedEventIdsToActiveEvents();
    syncSelectedMessageRangeToActiveEvents();
  } catch (error) {
    if(requestId !== loadSessionDetailRequestSeq){
      return;
    }
    state.detailError = normalizeRequestError(error, t('error.detail'));
  } finally {
    if(requestId === loadSessionDetailRequestSeq){
      clearDetailLoadingWatchdog();
      state.isDetailLoading = false;
      state.detailLoadMode = '';
      safeRenderActiveSession();
      if(effectiveLoadMode === 'refresh'){
        scheduleTodayUsageSummaryRefresh();
      }
    }
  }
}

async function refreshActiveSession(){
  if(!state.activePath) return;
  await openSession(state.activePath, { mode: 'refresh' });
  await loadTodayUsageSummary({ forceRefresh: true });
}

function isEditableTarget(target){
  if(!target || !(target instanceof Element)){
    return false;
  }
  if(target.closest('input, select, textarea, [contenteditable="true"]')){
    return true;
  }
  return false;
}

function focusShortcutSearch(){
  if(state.activeSession){
    if(detailPanelSection !== 'search'){
      setActiveDetailPanelSection('search', { skipSave: true });
    }
    const input = document.getElementById('detail_keyword_q');
    if(input && !input.disabled){
      input.focus();
      input.select();
      return;
    }
  }
  const input = document.getElementById('q');
  if(input){
    input.focus();
    input.select();
  }
}

function isShortcutDialogOpen(){
  const dialog = document.getElementById('shortcut_dialog');
  return !!dialog && !dialog.classList.contains('hidden');
}

function openShortcutDialog(){
  hideLabelPicker();
  const dialog = document.getElementById('shortcut_dialog');
  if(!dialog){
    return;
  }
  dialog.classList.remove('hidden');
  const closeButton = document.getElementById('close_shortcuts');
  if(closeButton){
    closeButton.focus();
  }
}

function closeShortcutDialog(){
  const dialog = document.getElementById('shortcut_dialog');
  if(!dialog){
    return;
  }
  const hadDialogFocus = dialog.contains(document.activeElement);
  dialog.classList.add('hidden');
  if(hadDialogFocus){
    const trigger = document.getElementById('open_shortcuts');
    if(trigger){
      trigger.focus();
    }
  }
}

function releaseSearchFocus(){
  const active = document.activeElement;
  if(!(active instanceof HTMLElement)){
    return false;
  }
  if(active.id === 'q' || active.id === 'cwd_q' || active.id === 'detail_keyword_q'){
    active.blur();
    return true;
  }
  return false;
}

function handleShortcutEscape(){
  let handled = false;
  if(isShortcutDialogOpen()){
    closeShortcutDialog();
    handled = true;
  }
  const picker = document.getElementById('label_picker');
  if(picker && !picker.classList.contains('hidden')){
    hideLabelPicker();
    handled = true;
  }
  if(releaseSearchFocus()){
    handled = true;
  }
  return handled;
}

function openRelativeSession(step){
  if(!Array.isArray(state.filtered) || state.filtered.length === 0){
    return false;
  }
  const currentIndex = state.filtered.findIndex(session => session.path === state.activePath);
  let nextIndex = currentIndex + step;
  if(currentIndex < 0){
    nextIndex = step > 0 ? 0 : state.filtered.length - 1;
  }
  if(nextIndex < 0 || nextIndex >= state.filtered.length){
    return false;
  }
  const nextSession = state.filtered[nextIndex];
  if(!nextSession || !nextSession.path || nextSession.path === state.activePath){
    return false;
  }
  openSession(nextSession.path, { mode: 'open' });
  const activeEl = document.querySelector('#sessions .session-item.active');
  if(activeEl){
    activeEl.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
  return true;
}

function triggerButtonShortcut(id){
  const button = document.getElementById(id);
  if(!(button instanceof HTMLButtonElement) || button.disabled){
    return false;
  }
  button.click();
  return true;
}

function triggerCheckboxShortcut(id){
  const checkbox = document.getElementById(id);
  if(!(checkbox instanceof HTMLInputElement) || checkbox.disabled || checkbox.type !== 'checkbox'){
    return false;
  }
  checkbox.click();
  return true;
}

async function triggerViewerRefresh(){
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
    loadSessionsTimer = null;
  }
  if(state.leftPaneTab === 'labels'){
    await loadLabels(false);
    await loadLabeledItems({ mode: 'reload' });
    if(state.activePath){
      await openSession(state.activePath, { mode: 'refresh' });
    }
    await loadTodayUsageSummary({ forceRefresh: true });
    return;
  }
  const loadMode = state.activePath ? 'reload_refresh' : 'reload';
  await loadSessions({ mode: loadMode });
  if(loadMode === 'reload_refresh' && state.activePath){
    await openSession(state.activePath, { mode: 'refresh' });
  }
  await loadTodayUsageSummary({ forceRefresh: true });
}

function moveDetailKeywordSearchByShortcut(step){
  if(!state.activeSession){
    return false;
  }
  const term = getDetailKeywordInputValue().trim();
  const previousTerm = detailKeywordSearchTerm;
  if(!term && !previousTerm){
    return false;
  }
  noteDetailInteraction();
  detailKeywordSearchTerm = term || previousTerm;
  const searchMeta = buildDetailKeywordSearchMeta(getDisplayEvents(), detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  if(!searchMeta.total){
    detailKeywordCurrentMatchIndex = -1;
    pendingDetailKeywordFocusIndex = -1;
    renderActiveSession();
    return true;
  }
  if(previousTerm !== detailKeywordSearchTerm || detailKeywordCurrentMatchIndex < 0 || detailKeywordCurrentMatchIndex >= searchMeta.total){
    detailKeywordCurrentMatchIndex = step > 0 ? 0 : searchMeta.total - 1;
  } else {
    detailKeywordCurrentMatchIndex = (detailKeywordCurrentMatchIndex + step + searchMeta.total) % searchMeta.total;
  }
  pendingDetailKeywordFocusIndex = detailKeywordCurrentMatchIndex;
  renderActiveSession();
  return true;
}

function safeBindById(id, eventName, handler){
  const node = document.getElementById(id);
  if(!node){
    return;
  }
  node.addEventListener(eventName, handler);
}

function bindDateTimePairChange(dateId, timeId, handler){
  const run = () => {
    syncDateTimeInputPairState(dateId, timeId);
    handler();
  };
  safeBindById(dateId, 'change', run);
  safeBindById(timeId, 'change', run);
}

function bindDatePaste(id){
  const input = document.getElementById(id);
  if(!input || input.dataset.datePasteReady === '1'){
    return;
  }
  input.dataset.datePasteReady = '1';
  input.addEventListener('paste', (event) => {
    const text = event.clipboardData ? event.clipboardData.getData('text') : '';
    if(text && applyDatePasteValue(input, text)){
      event.preventDefault();
      return;
    }
    setTimeout(() => {
      applyDatePasteValue(input, input.value || '');
    }, 0);
  });
}

function bindDateTimePairPaste(dateId, timeId){
  const dateInput = document.getElementById(dateId);
  const timeInput = document.getElementById(timeId);
  if(!dateInput || !timeInput){
    return;
  }
  const bindPaste = (input) => {
    if(!input || input.dataset.dateTimePasteReady === '1'){
      return;
    }
    input.dataset.dateTimePasteReady = '1';
    input.addEventListener('paste', (event) => {
      const text = event.clipboardData ? event.clipboardData.getData('text') : '';
      if(text && applyDateTimePairPasteValue(dateInput, timeInput, input, text)){
        event.preventDefault();
        return;
      }
      setTimeout(() => {
        applyDateTimePairPasteValue(dateInput, timeInput, input, input.value || '');
      }, 0);
    });
  };
  bindPaste(dateInput);
  bindPaste(timeInput);
}

safeBindById('cwd_q', 'input', applyFilter);
safeBindById('date_from', 'change', applyFilter);
safeBindById('date_to', 'change', applyFilter);
bindDateTimePairChange('event_date_from_date', 'event_date_from_time', applyFilter);
bindDateTimePairChange('event_date_to_date', 'event_date_to_time', applyFilter);
bindDatePaste('date_from');
bindDatePaste('date_to');
bindDateTimePairPaste('event_date_from_date', 'event_date_from_time');
bindDateTimePairPaste('event_date_to_date', 'event_date_to_time');
safeBindById('q', 'input', scheduleLoadSessions);
safeBindById('mode', 'change', scheduleLoadSessions);
safeBindById('source_filter', 'change', applyFilter);
safeBindById('subagents_filter', 'change', applyFilter);
document.querySelectorAll('.sort-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    setActiveSortOrder(tab.dataset.sort);
    scheduleLoadSessions();
  });
});
document.querySelectorAll('.left-pane-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    setActiveLeftPaneTab(tab.dataset.paneTab);
  });
});
safeBindById('session_label_filter', 'change', () => {
  updateLabelSelectColor('session_label_filter');
  scheduleLoadSessions();
});
safeBindById('event_label_filter', 'change', () => {
  updateLabelSelectColor('event_label_filter');
  scheduleLoadSessions();
});
safeBindById('detail_event_label_filter', 'change', () => {
  updateLabelSelectColor('detail_event_label_filter');
  saveFilters();
  renderActiveSession();
});
safeBindById('detail_cost_sort', 'change', () => {
  saveFilters();
  renderActiveSession();
});
bindDateTimePairChange('detail_event_date_from_date', 'detail_event_date_from_time', () => {
  saveFilters();
  renderActiveSession();
});
bindDateTimePairChange('detail_event_date_to_date', 'detail_event_date_to_time', () => {
  saveFilters();
  renderActiveSession();
});
bindDateTimePairPaste('detail_event_date_from_date', 'detail_event_date_from_time');
bindDateTimePairPaste('detail_event_date_to_date', 'detail_event_date_to_time');
safeBindById('clear_detail_event_date', 'click', () => {
  clearFpInstance('detail_event_date_from_date');
  clearFpInstance('detail_event_date_from_time');
  clearFpInstance('detail_event_date_to_date');
  clearFpInstance('detail_event_date_to_time');
  refreshDateTimeInputPairStates();
  saveFilters();
  renderActiveSession();
});
safeBindById('toggle_filters', 'click', () => {
  setFiltersVisible(!filtersVisible);
});
safeBindById('toggle_session_list_mobile', 'click', () => {
  setLeftPaneVisible(!leftPaneVisible);
});
safeBindById('toggle_detail_actions', 'click', () => {
  toggleDetailPanelSection('actions');
});
safeBindById('toggle_detail_search', 'click', () => {
  toggleDetailPanelSection('search');
});
safeBindById('toggle_detail_range', 'click', () => {
  toggleDetailPanelSection('range');
});
safeBindById('open_shortcuts', 'click', openShortcutDialog);
safeBindById('close_shortcuts', 'click', closeShortcutDialog);
safeBindById('toggle_meta', 'click', () => {
  setDetailMetaVisible(!detailMetaVisible);
});
safeBindById('reload', 'click', () => {
  triggerViewerRefresh();
});
safeBindById('clear', 'click', clearFilters);
safeBindById('only_user_instruction', 'change', () => {
  renderActiveSession();
});
safeBindById('only_ai_response', 'change', () => {
  renderActiveSession();
});
safeBindById('turn_boundary_only', 'change', () => {
  renderActiveSession();
});
safeBindById('only_token_usage', 'change', () => {
  renderActiveSession();
});
safeBindById('selected_events_only', 'change', () => {
  renderActiveSession();
});
safeBindById('reverse_order', 'change', () => {
  renderActiveSession();
});
safeBindById('clear_detail', 'click', clearDetailFilters);
safeBindById('refresh_detail', 'click', refreshActiveSession);
safeBindById('copy_resume_command', 'click', copyResumeCommand);
safeBindById('copy_displayed_messages', 'click', copyDisplayedMessages);
safeBindById('event_selection_mode', 'click', toggleEventSelectionMode);
safeBindById('copy_selected_messages', 'click', copySelectedMessages);
safeBindById('message_range_selection_mode', 'click', toggleMessageRangeSelectionMode);
safeBindById('clear_message_range_selection', 'click', clearDetailMessageRangeSelection);
safeBindById('detail_message_range_after', 'click', () => {
  applyDetailMessageRange('after');
});
safeBindById('detail_message_range_before', 'click', () => {
  applyDetailMessageRange('before');
});
safeBindById('detail_keyword_q', 'input', () => {
  updateDetailKeywordControls();
});
safeBindById('language_select', 'change', (event) => {
  setUiLanguage(event.target.value);
});
safeBindById('detail_keyword_q', 'keydown', (event) => {
  if(event.key === 'Enter' && !event.isComposing){
    event.preventDefault();
    runDetailKeywordSearch();
    releaseSearchFocus();
  }
});
safeBindById('detail_keyword_filter', 'click', applyDetailKeywordFilter);
safeBindById('detail_keyword_search', 'click', runDetailKeywordSearch);
safeBindById('detail_keyword_prev', 'click', () => {
  moveDetailKeywordSearch(-1);
});
safeBindById('detail_keyword_next', 'click', () => {
  moveDetailKeywordSearch(1);
});
safeBindById('detail_keyword_clear', 'click', clearDetailKeyword);
document.addEventListener('keydown', (event) => {
  if(event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey){
    return;
  }
  if(event.key === 'Escape'){
    if(handleShortcutEscape()){
      event.preventDefault();
    }
    return;
  }
  if(isEditableTarget(event.target)){
    return;
  }
  if(event.key === 'F5'){
    event.preventDefault();
    triggerViewerRefresh();
    return;
  }
  if(event.key === '/'){
    event.preventDefault();
    focusShortcutSearch();
    return;
  }
  if(event.shiftKey){
    if(event.code === 'KeyF'){
      if(triggerButtonShortcut('toggle_filters')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyL'){
      if(triggerButtonShortcut('clear')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyD'){
      if(triggerButtonShortcut('clear_detail')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyT'){
      if(triggerButtonShortcut('toggle_detail_actions')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyR'){
      if(triggerButtonShortcut('copy_resume_command')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyC'){
      if(triggerButtonShortcut('copy_displayed_messages')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyS'){
      if(triggerButtonShortcut('event_selection_mode')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyX'){
      if(triggerButtonShortcut('copy_selected_messages')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyG'){
      if(triggerButtonShortcut('message_range_selection_mode')){
        event.preventDefault();
      }
      return;
    }
    if(event.code === 'KeyH'){
      if(triggerButtonShortcut('clear_message_range_selection')){
        event.preventDefault();
      }
      return;
    }
    return;
  }
  if(event.code === 'Digit1'){
    if(triggerCheckboxShortcut('only_user_instruction')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Digit2'){
    if(triggerCheckboxShortcut('only_ai_response')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Digit3'){
    if(triggerCheckboxShortcut('turn_boundary_only')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Digit4'){
    if(triggerCheckboxShortcut('reverse_order')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Digit5'){
    if(triggerCheckboxShortcut('only_token_usage')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Comma'){
    if(triggerButtonShortcut('detail_message_range_before')){
      event.preventDefault();
    }
    return;
  }
  if(event.code === 'Period'){
    if(triggerButtonShortcut('detail_message_range_after')){
      event.preventDefault();
    }
    return;
  }
  const key = event.key.toLowerCase();
  if(event.code === 'KeyM' || key === 'm'){
    event.preventDefault();
    setDetailMetaVisible(!detailMetaVisible);
    return;
  }
  if(event.key === '[' || event.code === 'BracketLeft'){
    if(openRelativeSession(-1)){
      event.preventDefault();
    }
    return;
  }
  if(event.key === ']' || event.code === 'BracketRight'){
    if(openRelativeSession(1)){
      event.preventDefault();
    }
    return;
  }
  if(key === 'n'){
    if(moveDetailKeywordSearchByShortcut(1)){
      event.preventDefault();
    }
    return;
  }
  if(key === 'p'){
    if(moveDetailKeywordSearchByShortcut(-1)){
      event.preventDefault();
    }
  }
});
document.getElementById('add_session_label').addEventListener('click', async (event) => {
  await addSessionLabelFromButton(event.currentTarget);
});
document.getElementById('events').addEventListener('pointerdown', (event) => {
  if(!event.target.closest('.ev')){
    return;
  }
  noteDetailInteraction();
  if(event.target.closest('pre')){
    detailPointerDown = true;
  }
});
document.getElementById('events').addEventListener('click', (event) => {
  const target = event.target;
  const addLabelBtn = target.closest('.event-label-add-button');
  if(addLabelBtn){
    addEventLabelFromButton(addLabelBtn, addLabelBtn.dataset.eventId);
    return;
  }
  const copyBtn = target.closest('.event-copy-button');
  if(copyBtn){
    copyEventMessage(copyBtn, copyBtn.dataset.eventId);
    return;
  }
  const removeBtn = target.closest('.label-remove-button[data-remove-type="event"]');
  if(removeBtn){
    removeEventLabel(removeBtn.dataset.eventId, Number(removeBtn.dataset.labelId));
    return;
  }
  const toggleBtn = target.closest('.ev-body-toggle');
  if(toggleBtn){
    noteDetailInteraction();
    const wrap = toggleBtn.closest('.ev-body-wrap');
    if(!wrap) return;
    const isCollapsed = wrap.classList.toggle('collapsed');
    const eventKey = wrap.dataset.eventKey || '';
    setDetailEventBodyExpanded(state.activePath, eventKey, !isCollapsed);
    toggleBtn.textContent = isCollapsed ? t('detail.bodyExpand') : t('detail.bodyCollapse');
    return;
  }
});
document.getElementById('events').addEventListener('change', (event) => {
  const target = event.target;
  if(target.classList.contains('event-select-checkbox')){
    updateEventSelection(target.dataset.eventId, target.checked, target.closest('.ev'));
    return;
  }
  if(target.classList.contains('event-range-radio') && target.checked){
    updateMessageRangeSelection(target.dataset.eventId);
    return;
  }
});
document.getElementById('sessions').addEventListener('click', (event) => {
  const item = event.target.closest('.session-item');
  if(item && item.dataset.path){
    openSession(item.dataset.path);
  }
});
document.getElementById('labeled_items').addEventListener('click', async (event) => {
  const item = event.target.closest('.labeled-item');
  if(!item || !item.dataset.path){
    return;
  }
  if(item.dataset.itemKind === 'event' && item.dataset.eventId){
    await openLabeledEvent(item.dataset.path, item.dataset.eventId);
    return;
  }
  openSession(item.dataset.path, { mode: 'open' });
});
window.addEventListener('pointerup', () => {
  if(!detailPointerDown){
    return;
  }
  detailPointerDown = false;
  noteDetailInteraction();
  scheduleDeferredAutomaticDetailSync();
});
document.addEventListener('selectionchange', () => {
  if(hasDetailTextSelection()){
    noteDetailInteraction();
    return;
  }
  scheduleDeferredAutomaticDetailSync();
});
document.getElementById('open_label_manager').addEventListener('click', openLabelManagerWindow);
safeBindById('open_cost_summary', 'click', openCostSummaryWindow);
safeBindById('currency_select', 'change', (event) => {
  setCostCurrency(event.target.value);
});
document.addEventListener('click', (event) => {
  const picker = document.getElementById('label_picker');
  if(picker.classList.contains('hidden')) return;
  if(picker.contains(event.target)) return;
  if(event.target.closest('.event-label-add-button')) return;
  if(event.target.closest('#add_session_label')) return;
  hideLabelPicker();
});
document.getElementById('shortcut_dialog').addEventListener('click', (event) => {
  if(event.target.id === 'shortcut_dialog'){
    closeShortcutDialog();
  }
});
window.addEventListener('message', async (event) => {
  if(event.origin !== location.origin) return;
  if(labelManagerWindow && !labelManagerWindow.closed && event.source !== labelManagerWindow) return;
  if(!event.data || event.data.type !== 'labels-updated') return;
  await loadLabels(false);
  await refreshLabeledViews();
});
window.addEventListener('storage', (event) => {
  if(event.key === LANGUAGE_STORAGE_KEY){
    const nextLanguage = normalizeLanguage(event.newValue || 'ja');
    if(nextLanguage !== uiLanguage){
      setUiLanguage(nextLanguage, false);
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
      setTodayUsageFromCostSummary(cached.data);
      renderTodayUsageSummary();
    }
  }
});
window.addEventListener('resize', () => {
  updateCompactUiMode();
  updateLeftPaneVisibility();
});
updateCopyResumeButtonState();
updateDisplayedMessagesCopyButtonState();
updateEventSelectionModeButtonState();
updateCopySelectedMessagesButtonState();
updateMessageRangeSelectionModeButtonState();
updateClearMessageRangeSelectionButtonState();
updateMessageRangeFilterButtonsState();
updateDetailKeywordControls({ total: 0 });
updateRefreshDetailButtonState();
updateFilterVisibility();
restoreFilters();
initSegmentedInputs();
initAllFlatpickr();
setUiLanguage(getRequestedLanguage(), false);
setCostCurrency(getRequestedCostCurrency(), false);
updateFilterVisibility();
updateCompactUiMode();
updateDetailMetaVisibility();
updateLeftPaneVisibility();
renderLeftPaneTabs();
renderLabeledList();
updateDetailActionsVisibility();
state.isSessionsLoading = true;
renderSessionList();
renderTodayUsageSummary();
scheduleTodayUsageSummaryRefresh();
loadLabels(false)
  .catch(() => {})
  .finally(() => {
    loadSessions({ mode: 'initial' });
    if(state.leftPaneTab === 'labels'){
      void loadLabeledItems({ mode: 'initial' });
    }
  });
