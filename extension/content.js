function showToast(msg, type) {
    var existing = document.getElementById('codetrace-toast');
    if (existing) existing.remove();

    var toast = document.createElement('div');
    toast.id = 'codetrace-toast';
    toast.textContent = msg;
    var bg = type === 'error' ? '#f85149' : '#238636';
    toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);padding:8px 20px;border-radius:8px;font-size:14px;z-index:99999;color:#fff;background:' + bg + ';box-shadow:0 4px 12px rgba(0,0,0,0.3);';
    document.body.appendChild(toast);
    setTimeout(function () { toast.remove(); }, 3000);
}

function getFileInfo() {
    const pathname = window.location.pathname;
    // GitHub 文件页格式：/owner/repo/blob/branch/path/to/file
    const match = pathname.match(/^\/([^/]+)\/([^/]+)\/blob\/[^/]+\/(.+)/);
    if (!match) return null;
    return {
        owner: match[1],
        repo: match[2],
        filePath: match[3],
        repoFull: `${match[1]}/${match[2]}`,
    };
}

function injectButton() {
    var info = getFileInfo();
    if (!info) return false;

    // 找到 "Raw" 按钮，通过它定位到按钮组
    var rawLink = [...document.querySelectorAll('a, button')].find(function(el) {
        return el.textContent.trim() === 'Raw';
    });
    if (!rawLink) return false;

    // rawLink → .Item → .ButtonGroup，我们要把新按钮加到 ButtonGroup 里
    var group = rawLink.parentElement;
    if (group) group = group.parentElement;
    if (!group) return false;

    // 防止重复插入
    if (document.getElementById('codetrace-btn')) return true;

    var item = document.createElement('div');
    item.style.cssText = 'display:inline-flex;align-items:center;';

    var btn = document.createElement('button');
    btn.id = 'codetrace-btn';
    btn.textContent = '追溯';
    btn.className = rawLink.className;
    btn.style.padding = '0 6px';
    btn.title = 'CodeTrace 代码变更追溯';
    btn.onclick = function() { handleTrace(info); };

    item.appendChild(btn);
    group.appendChild(item);
    return true;
}

const API_BASE = 'http://localhost:8000';

async function handleTrace(info) {
    const functionName = prompt('函数名（留空则追溯整个文件）:');
    if (functionName === null) return; // 用户点了取消

    showToast('CodeTrace 正在追溯...', 'info');

    try {
        const url = functionName
            ? `${API_BASE}/api/trace/function?function_name=${encodeURIComponent(functionName)}`
            : `${API_BASE}/api/trace`;

        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                repo_url: `https://github.com/${info.repoFull}.git`,
                file_path: info.filePath,
            }),
        });

        if (!res.ok) throw new Error('请求失败');
        const data = await res.json();
        showResult(data, functionName);
    } catch (e) {
        showToast('追溯失败: ' + e.message, 'error');
    }
}

function showResult(data, functionName) {
    removeResult();

    const isFunctionMode = 'function_name' in data;
    const nodes = isFunctionMode ? data.history : data.timeline;

    const panel = document.createElement('div');
    panel.id = 'codetrace-panel';
    panel.style.cssText = 'position:fixed;top:0;right:0;width:420px;height:100vh;background:#161b22;border-left:1px solid #30363d;z-index:9999;overflow-y:auto;color:#c9d1d9;font-size:13px;box-shadow:-4px 0 16px rgba(0,0,0,0.4);';

    const close = document.createElement('div');
    close.textContent = '✕';
    close.style.cssText = 'position:sticky;top:0;text-align:right;padding:10px 14px;cursor:pointer;background:#161b22;border-bottom:1px solid #21262d;font-size:16px;';
    close.onclick = removeResult;

    const header = document.createElement('div');
    header.style.cssText = 'padding:10px 14px;border-bottom:1px solid #21262d;';
    header.innerHTML = `<strong>${data.repo}</strong>` + ' · ' + data.file_path + (isFunctionMode ? ' · 函数 <strong>' + data.function_name + '</strong>' : '') + ' · ' + data.commit_count + ' 次提交';

    panel.appendChild(close);
    panel.appendChild(header);

    if (!nodes || nodes.length === 0) {
        const empty = document.createElement('div');
        empty.style.cssText = 'padding:20px;color:#d69e2e;text-align:center;';
        empty.textContent = data.note || '无数据';
        panel.appendChild(empty);
    } else {
        nodes.forEach(function (node) {
            panel.appendChild(buildNodeCard(node, isFunctionMode));
        });
    }

    document.body.appendChild(panel);
}

function removeResult() {
    var existing = document.getElementById('codetrace-panel');
    if (existing) existing.remove();
}

function buildNodeCard(node, showFunction) {
    var card = document.createElement('div');
    card.style.cssText = 'padding:10px 14px;border-bottom:1px solid #21262d;';

    var func = node.function;
    var changeType = node.change_type;

    var colors = {
        feature: '#2ea043',
        bugfix: '#f85149',
        refactor: '#a371f7',
        chore: '#8b949e',
        docs: '#58a6ff',
        test: '#79c0ff'
    };

    var hashPart = node.commit_hash ? '<code style="color:#7ee787">' + node.commit_hash.slice(0, 7) + '</code>' : '';
    var datePart = (node.date || '').slice(0, 10);
    var typeTag = changeType
        ? '<span style="padding:1px 6px;border-radius:4px;font-size:11px;background:' + (colors[changeType] || '#8b949e') + ';color:#fff">' + changeType + '</span>'
        : '';
    var funcTag = func
        ? '<code style="color:#d2a8ff;font-size:12px">' + func.name + ' (L' + func.start_line + ')</code>'
        : '';
    var llmNote = node.llm_note
        ? '<div style="color:#d69e2e;font-size:12px;margin-top:2px">⚡ ' + node.llm_note + '</div>'
        : '';
    var prInfo = node.pr_title
        ? '<div style="margin-top:2px"><span style="color:#58a6ff;font-size:12px">PR: ' + node.pr_title + '</span></div>'
        : '';
    var diffInfo = node.diff_stats
        ? '<div style="color:#8b949e;font-size:12px">+' + node.diff_stats.additions + '/-' + node.diff_stats.deletions + ' · ' + node.diff_stats.files_changed + ' 个文件</div>'
        : '';

    card.innerHTML =
        '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">' +
        hashPart + ' ' +
        '<span style="color:#8b949e">' + node.author + ' · ' + datePart + '</span> ' +
        typeTag + ' ' + funcTag +
        '</div>' +
        '<div style="color:#c9d1d9;margin-bottom:4px">' + (node.summary || (func ? func.name + ' 变更' : '')) + '</div>' +
        '<div style="color:#8b949e;font-size:12px">' + (node.message || '') + '</div>' +
        llmNote + prInfo + diffInfo;

    return card;
}

// GitHub 是 SPA，页面异步渲染且用户可能在仓库页和文件页之间导航
// 用 MutationObserver 监听 DOM 变化，一旦出现 "Raw" 按钮就注入
var observer = new MutationObserver(function() {
  injectButton();
});
observer.observe(document.body, { childList: true, subtree: true });

// 初始调用一次
injectButton();