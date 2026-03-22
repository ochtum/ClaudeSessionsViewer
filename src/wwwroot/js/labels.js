(() => {
const LANGUAGE_STORAGE_KEY = 'claude_sessions_viewer_language_v1';
const SUPPORTED_LANGUAGES = ['ja', 'en', 'zh-Hans', 'zh-Hant'];
const LABEL_I18N = {
  ja: {
    'language.selector': '言語',
    'page.title': 'ラベル管理 | Claude Sessions Viewer',
    'page.heroTitle': 'ラベル管理',
    'page.heroCopy': 'セッションとイベントに共通で使うラベルをここで整えます。色コードを直接入力するか、プリセットをクリックして素早く設定できます。',
    'editor.kicker': 'Label Editor',
    'editor.title': '新規作成 / 編集',
    'editor.copy': '保存すると一覧フィルタと詳細画面の両方にすぐ反映されます。',
    'editor.chip': '即時反映',
    'form.name': 'ラベル名',
    'form.color': '色コード',
    'form.presets': '色プリセット',
    'form.save': '保存',
    'form.name.placeholder': '例: README / 画像 / 再確認',
    'form.color.placeholder': '#3b82f6 / rgb(...) / oklch(...)',
    'list.kicker': 'Registered Labels',
    'list.title': '既存ラベル',
    'list.count': '{count}件',
    'list.empty': 'ラベルはまだありません。上のフォームから最初のラベルを作成してください。',
    'list.colorPrefix': 'color',
    'action.edit': '編集',
    'action.delete': '削除',
    'dialog.validation.kicker': '入力チェック',
    'dialog.validation.title': '入力エラー',
    'dialog.error.kicker': 'エラーメッセージ',
    'dialog.error.title': 'エラー',
    'dialog.close': '閉じる',
    'confirm.delete': 'このラベルを削除しますか？',
    'preset.red': '赤系',
    'preset.blue': '青系',
    'preset.green': '緑系',
    'preset.yellow': '黄色系',
    'preset.purple': '紫系',
    'error.loadFailed': 'ラベル一覧の取得に失敗しました。',
    'error.saveFailed': 'ラベルの保存に失敗しました。',
    'error.deleteFailed': 'ラベルの削除に失敗しました。',
    'server.colorInvalid': '色コードの形式が不正です',
    'server.colorRequired': '色コードを入力してください',
    'server.nameRequired': 'ラベル名を入力してください',
    'server.nameTooLong': 'ラベル名が長すぎます',
    'server.labelMissing': 'ラベルが見つかりません',
    'server.labelDuplicate': '同名のラベルは既に存在します',
    'server.notFound': '見つかりません',
    'server.labelIdRequired': 'ラベルIDが必要です',
  },
  en: {
    'language.selector': 'Language',
    'page.title': 'Label Manager | Claude Sessions Viewer',
    'page.heroTitle': 'Label Manager',
    'page.heroCopy': 'Manage the shared labels used across sessions and events. Enter a color directly or click a preset for quick setup.',
    'editor.kicker': 'Label Editor',
    'editor.title': 'Create / Edit',
    'editor.copy': 'Saving updates both the list filters and the detail view immediately.',
    'editor.chip': 'Live update',
    'form.name': 'Label name',
    'form.color': 'Color value',
    'form.presets': 'Color presets',
    'form.save': 'Save',
    'form.name.placeholder': 'Example: README / Images / Recheck',
    'form.color.placeholder': '#3b82f6 / rgb(...) / oklch(...)',
    'list.kicker': 'Registered Labels',
    'list.title': 'Existing labels',
    'list.count': '{count} labels',
    'list.empty': 'No labels yet. Create your first label from the form above.',
    'list.colorPrefix': 'color',
    'action.edit': 'Edit',
    'action.delete': 'Delete',
    'dialog.validation.kicker': 'Validation',
    'dialog.validation.title': 'Input error',
    'dialog.error.kicker': 'Error message',
    'dialog.error.title': 'Error',
    'dialog.close': 'Close',
    'confirm.delete': 'Delete this label?',
    'preset.red': 'Red',
    'preset.blue': 'Blue',
    'preset.green': 'Green',
    'preset.yellow': 'Yellow',
    'preset.purple': 'Purple',
    'error.loadFailed': 'Failed to load labels.',
    'error.saveFailed': 'Failed to save the label.',
    'error.deleteFailed': 'Failed to delete the label.',
    'server.colorInvalid': 'The color value format is invalid.',
    'server.colorRequired': 'Enter a color value.',
    'server.nameRequired': 'Enter a label name.',
    'server.nameTooLong': 'The label name is too long.',
    'server.labelMissing': 'The label was not found.',
    'server.labelDuplicate': 'A label with the same name already exists.',
    'server.notFound': 'Not found.',
    'server.labelIdRequired': 'A label ID is required.',
  },
  'zh-Hans': {
    'language.selector': '语言',
    'page.title': '标签管理 | Claude Sessions Viewer',
    'page.heroTitle': '标签管理',
    'page.heroCopy': '在这里整理会话与事件共用的标签。可以直接输入颜色值，也可以点击预设快速设置。',
    'editor.kicker': 'Label Editor',
    'editor.title': '新建 / 编辑',
    'editor.copy': '保存后会立即反映到列表筛选和详情视图。',
    'editor.chip': '即时生效',
    'form.name': '标签名',
    'form.color': '颜色值',
    'form.presets': '颜色预设',
    'form.save': '保存',
    'form.name.placeholder': '例如: README / 图片 / 再确认',
    'form.color.placeholder': '#3b82f6 / rgb(...) / oklch(...)',
    'list.kicker': 'Registered Labels',
    'list.title': '现有标签',
    'list.count': '{count} 个标签',
    'list.empty': '还没有标签。请先在上面的表单中创建第一个标签。',
    'list.colorPrefix': 'color',
    'action.edit': '编辑',
    'action.delete': '删除',
    'dialog.validation.kicker': '输入检查',
    'dialog.validation.title': '输入错误',
    'dialog.error.kicker': '错误信息',
    'dialog.error.title': '错误',
    'dialog.close': '关闭',
    'confirm.delete': '要删除这个标签吗？',
    'preset.red': '红色系',
    'preset.blue': '蓝色系',
    'preset.green': '绿色系',
    'preset.yellow': '黄色系',
    'preset.purple': '紫色系',
    'error.loadFailed': '获取标签列表失败。',
    'error.saveFailed': '保存标签失败。',
    'error.deleteFailed': '删除标签失败。',
    'server.colorInvalid': '颜色值格式无效。',
    'server.colorRequired': '请输入颜色值。',
    'server.nameRequired': '请输入标签名。',
    'server.nameTooLong': '标签名过长。',
    'server.labelMissing': '未找到标签。',
    'server.labelDuplicate': '已存在同名标签。',
    'server.notFound': '未找到。',
    'server.labelIdRequired': '需要标签 ID。',
  },
};
LABEL_I18N['zh-Hant'] = {
  ...LABEL_I18N['zh-Hans'],
  'language.selector': '語言',
  'page.title': '標籤管理 | Claude Sessions Viewer',
  'page.heroTitle': '標籤管理',
  'page.heroCopy': '在這裡整理工作階段與事件共用的標籤。可以直接輸入顏色值，也可以點擊預設快速設定。',
  'editor.title': '新增 / 編輯',
  'editor.copy': '儲存後會立即反映到列表篩選與詳情視圖。',
  'editor.chip': '即時生效',
  'form.name': '標籤名',
  'form.color': '顏色值',
  'form.presets': '顏色預設',
  'form.save': '儲存',
  'form.name.placeholder': '例如: README / 圖片 / 再確認',
  'list.title': '現有標籤',
  'list.count': '{count} 個標籤',
  'list.empty': '還沒有標籤。請先在上面的表單中建立第一個標籤。',
  'action.edit': '編輯',
  'action.delete': '刪除',
  'dialog.validation.kicker': '輸入檢查',
  'dialog.validation.title': '輸入錯誤',
  'dialog.error.kicker': '錯誤訊息',
  'dialog.error.title': '錯誤',
  'dialog.close': '關閉',
  'confirm.delete': '要刪除這個標籤嗎？',
  'preset.red': '紅色系',
  'preset.blue': '藍色系',
  'preset.green': '綠色系',
  'preset.yellow': '黃色系',
  'preset.purple': '紫色系',
  'error.loadFailed': '取得標籤列表失敗。',
  'error.saveFailed': '儲存標籤失敗。',
  'error.deleteFailed': '刪除標籤失敗。',
  'server.colorInvalid': '顏色值格式無效。',
  'server.colorRequired': '請輸入顏色值。',
  'server.nameRequired': '請輸入標籤名。',
  'server.nameTooLong': '標籤名過長。',
  'server.labelMissing': '找不到標籤。',
  'server.labelDuplicate': '已存在同名標籤。',
  'server.notFound': '找不到項目。',
  'server.labelIdRequired': '需要標籤 ID。',
};
const PRESETS = {
  red: { color: '#ef4444' },
  blue: { color: '#3b82f6' },
  green: { color: '#22c55e' },
  yellow: { color: '#eab308' },
  purple: { color: '#a855f7' },
};
const SERVER_ERROR_KEYS = {
  '色コードの形式が不正です': 'server.colorInvalid',
  '色コードを入力してください': 'server.colorRequired',
  'ラベル名を入力してください': 'server.nameRequired',
  'ラベル名が長すぎます': 'server.nameTooLong',
  'ラベルが見つかりません': 'server.labelMissing',
  '同名のラベルは既に存在します': 'server.labelDuplicate',
  'not found': 'server.notFound',
  'label id is required': 'server.labelIdRequired',
};

let uiLanguage = 'ja';
let labelItems = [];
let errorDialogTone = 'validation';
let errorDialogMessage = '';

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

function isLabelManagerPage(){
  return !!document.getElementById('label_list') && !!document.getElementById('save_label');
}

function getStoredLanguage(){
  try {
    return localStorage.getItem(LANGUAGE_STORAGE_KEY) || '';
  } catch (e) {
    return '';
  }
}

function getRequestedLanguage(){
  const params = new URLSearchParams(window.location.search);
  return normalizeLanguage(params.get('lang') || getStoredLanguage() || uiLanguage);
}

function t(key, vars){
  const dict = LABEL_I18N[uiLanguage] || LABEL_I18N.ja;
  let text = dict[key];
  if(typeof text !== 'string'){
    text = LABEL_I18N.ja[key] || key;
  }
  if(vars){
    Object.entries(vars).forEach(([name, value]) => {
      text = text.replaceAll(`{${name}}`, String(value));
    });
  }
  return text;
}

function esc(s){
  return (s ?? '').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function presetLabel(family){
  if(!family) return '';
  return t(`preset.${family}`);
}

function translateServerError(message){
  const key = SERVER_ERROR_KEYS[message || ''];
  return key ? t(key) : (message || '');
}

function badgeHtml(label){
  return `<span class="badge label-badge" style="--label-color:${esc(label.color_value)}"><span class="dot"></span><span>${esc(label.name)}</span></span>`;
}

function renderLabelCount(count){
  if(uiLanguage === 'en' && count === 1){
    return '1 label';
  }
  return t('list.count', { count });
}

function applyDialogLanguage(){
  const validation = errorDialogTone !== 'error';
  document.getElementById('error_dialog_kicker').textContent = validation ? t('dialog.validation.kicker') : t('dialog.error.kicker');
  document.getElementById('error_dialog_title').textContent = validation ? t('dialog.validation.title') : t('dialog.error.title');
  document.getElementById('error_dialog_message').textContent = translateServerError(errorDialogMessage);
  document.getElementById('error_dialog_close').textContent = t('dialog.close');
}

function applyLabelLanguage(){
  document.documentElement.lang = uiLanguage;
  document.title = t('page.title');
  document.getElementById('language_select').value = uiLanguage;
  document.getElementById('language_select').setAttribute('aria-label', t('language.selector'));
  document.getElementById('hero_title').textContent = t('page.heroTitle');
  document.getElementById('hero_copy').textContent = t('page.heroCopy');
  document.getElementById('editor_kicker').textContent = t('editor.kicker');
  document.getElementById('editor_title').textContent = t('editor.title');
  document.getElementById('editor_copy').textContent = t('editor.copy');
  document.getElementById('editor_chip').textContent = t('editor.chip');
  document.getElementById('label_name_text').textContent = t('form.name');
  document.getElementById('label_color_text').textContent = t('form.color');
  document.getElementById('preset_field_title').textContent = t('form.presets');
  document.getElementById('save_label').textContent = t('form.save');
  document.getElementById('label_name').placeholder = t('form.name.placeholder');
  document.getElementById('label_color').placeholder = t('form.color.placeholder');
  document.getElementById('list_kicker').textContent = t('list.kicker');
  document.getElementById('list_title').textContent = t('list.title');
  applyDialogLanguage();
  renderPresetPreview();
  renderLabelList();
}

function setUiLanguage(nextLanguage, persist){
  uiLanguage = normalizeLanguage(nextLanguage);
  if(persist !== false){
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, uiLanguage);
    } catch (e) {
      // Ignore storage write errors.
    }
  }
  applyLabelLanguage();
}

function showErrorDialog(message, tone){
  errorDialogTone = tone === 'error' ? 'error' : 'validation';
  errorDialogMessage = message || '';
  applyDialogLanguage();
  document.getElementById('error_dialog').classList.remove('hidden');
}

function hideErrorDialog(){
  document.getElementById('error_dialog').classList.add('hidden');
}

function notifyParent(){
  if(window.opener && !window.opener.closed){
    window.opener.postMessage({ type: 'labels-updated' }, location.origin);
  }
}

async function postJson(url, payload){
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  return r.json();
}

function renderPresetPreview(){
  const box = document.getElementById('preset_preview');
  const selectedFamily = document.getElementById('label_family').value || '';
  box.innerHTML = Object.entries(PRESETS).map(([key, value]) =>
    `<button type="button" class="badge preset-badge ${selectedFamily === key ? 'active' : ''}" data-family="${esc(key)}" data-color="${esc(value.color)}" style="--label-color:${esc(value.color)}"><span class="dot"></span><span>${esc(presetLabel(key))}</span></button>`
  ).join('');
  box.querySelectorAll('.preset-badge').forEach(button => {
    button.onclick = () => {
      document.getElementById('label_color').value = button.dataset.color || '';
      document.getElementById('label_family').value = button.dataset.family || '';
      renderPresetPreview();
    };
  });
}

function resetForm(){
  document.getElementById('label_id').value = '';
  document.getElementById('label_name').value = '';
  document.getElementById('label_color').value = '';
  document.getElementById('label_family').value = '';
  renderPresetPreview();
}

function editLabel(label){
  document.getElementById('label_id').value = label.id;
  document.getElementById('label_name').value = label.name;
  document.getElementById('label_color').value = label.color_value;
  document.getElementById('label_family').value = label.color_family || '';
  renderPresetPreview();
}

function renderLabelList(){
  const list = document.getElementById('label_list');
  const countBadge = document.getElementById('label_count_badge');
  countBadge.textContent = renderLabelCount(labelItems.length);
  if(!labelItems.length){
    list.innerHTML = `<div class="empty-state">${esc(t('list.empty'))}</div>`;
    return;
  }
  list.innerHTML = labelItems.map(label => {
    const familyText = label.color_family ? ` / ${esc(presetLabel(label.color_family))}` : '';
    return `
      <div class="label-row">
        <div class="label-main">
          <div class="label-topline">
            ${badgeHtml(label)}
            <div class="label-meta"><span class="label-meta-prefix">${esc(t('list.colorPrefix'))}</span><span class="label-code">${esc(label.color_value)}</span>${familyText}</div>
          </div>
        </div>
        <div class="label-row-actions">
          <button class="secondary edit-label" data-label-id="${esc(label.id)}">${esc(t('action.edit'))}</button>
          <button class="danger delete-label" data-label-id="${esc(label.id)}">${esc(t('action.delete'))}</button>
        </div>
      </div>
    `;
  }).join('');
  list.querySelectorAll('.edit-label').forEach(button => {
    button.onclick = () => {
      const label = labelItems.find(item => String(item.id) === button.dataset.labelId);
      if(label) editLabel(label);
    };
  });
  list.querySelectorAll('.delete-label').forEach(button => {
    button.onclick = async () => {
      await deleteLabel(Number(button.dataset.labelId));
    };
  });
}

async function deleteLabel(id){
  if(!confirm(t('confirm.delete'))) return;
  try {
    const data = await postJson('/api/labels/delete', { id });
    if(data.error){
      showErrorDialog(data.error, 'error');
      return;
    }
    notifyParent();
    await loadLabels();
    resetForm();
  } catch (error) {
    showErrorDialog(t('error.deleteFailed'), 'error');
  }
}

async function loadLabels(){
  try {
    const r = await fetch('/api/labels?ts=' + Date.now(), { cache: 'no-store' });
    const data = await r.json();
    labelItems = data.labels || [];
    renderLabelList();
  } catch (error) {
    showErrorDialog(t('error.loadFailed'), 'error');
  }
}

function initLabelManagerPage(){
  if(!isLabelManagerPage() || window.__claudeLabelManagerInitialized){
    return;
  }

  window.__claudeLabelManagerInitialized = true;

  document.getElementById('save_label').addEventListener('click', async () => {
    const payload = {
      id: document.getElementById('label_id').value || null,
      name: document.getElementById('label_name').value,
      color_value: document.getElementById('label_color').value,
      color_family: document.getElementById('label_family').value,
    };
    try {
      const data = await postJson('/api/labels/save', payload);
      if(data.error){
        showErrorDialog(data.error, 'validation');
        return;
      }
      notifyParent();
      await loadLabels();
      resetForm();
    } catch (error) {
      showErrorDialog(t('error.saveFailed'), 'error');
    }
  });

  document.getElementById('language_select').addEventListener('change', (event) => {
    setUiLanguage(event.target.value);
  });
  document.getElementById('error_dialog_close').addEventListener('click', hideErrorDialog);
  document.getElementById('error_dialog').addEventListener('click', (event) => {
    if(event.target.id === 'error_dialog'){
      hideErrorDialog();
    }
  });
  document.addEventListener('keydown', (event) => {
    if(event.key === 'Escape'){
      hideErrorDialog();
    }
  });
  document.getElementById('label_color').addEventListener('input', () => {
    const color = document.getElementById('label_color').value.trim().toLowerCase();
    const matched = Object.entries(PRESETS).find(([, value]) => value.color.toLowerCase() === color);
    document.getElementById('label_family').value = matched ? matched[0] : '';
    renderPresetPreview();
  });
  window.addEventListener('storage', (event) => {
    if(event.key !== LANGUAGE_STORAGE_KEY){
      return;
    }
    const nextLanguage = normalizeLanguage(event.newValue || 'ja');
    if(nextLanguage !== uiLanguage){
      setUiLanguage(nextLanguage, false);
    }
  });

  setUiLanguage(getRequestedLanguage(), false);
  loadLabels();
}

if(document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', initLabelManagerPage, { once: true });
} else {
  initLabelManagerPage();
}
})();
