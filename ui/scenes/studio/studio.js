
  var workspace = document.getElementById('workspace');
  var planForm = document.getElementById('planForm');
  var topicInput = document.getElementById('topicInput');
  var audienceInput = document.getElementById('audienceInput');
  var durationInput = document.getElementById('durationInput');
  var styleGrid = document.getElementById('styleGrid');
  var colorGrid = document.getElementById('colorGrid');
  var lookSummary = document.getElementById('lookSummary');
  var customInput = document.getElementById('customInput');
  var styleChoices = [];
  var colorChoices = [];
  var selectedStyle = null;
  var selectedColor = null;
  var planBtn = document.getElementById('planBtn');
  var planBtnText = document.getElementById('planBtnText');
  var produceBtn = document.getElementById('produceBtn');
  var produceBtnText = document.getElementById('produceBtnText');
  var sampleBtn = document.getElementById('sampleBtn');
  var keyRow = document.getElementById('keyRow');
  var keyInput = document.getElementById('keyInput');
  var keyBtn = document.getElementById('keyBtn');
  var errorLine = document.getElementById('errorLine');
  var fixLine = document.getElementById('fixLine');
  var statusPill = document.getElementById('statusPill');
  var statusText = document.getElementById('statusText');
  var stateAnnouncer = document.getElementById('stateAnnouncer');
  var previewFrame = document.getElementById('previewFrame');
  var outputVideo = document.getElementById('outputVideo');
  var previewState = document.getElementById('previewState');
  var previewIcon = document.getElementById('previewIcon');
  var previewText = document.getElementById('previewText');
  var thinkingPanel = document.getElementById('thinkingPanel');
  var thinkingWord = document.getElementById('thinkingWord');
  var thinkingDetail = document.getElementById('thinkingDetail');
  var thinkingNote = document.getElementById('thinkingNote');
  var thinkingContext = document.getElementById('thinkingContext');
  var motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  var thinkingTimer = null;
  var thinkingPhase = null;
  var thinkingStarted = 0;
  var thinkingMessages = {
    planning: ['Planning scenes', 'Turning your brief into a scene-by-scene story.', 'You’ll review the script and visuals before production.'],
    preparing: ['Getting ready', 'Getting your approved scenes ready to produce.', 'Your script stays just as you approved it.'],
    scenes: ['Imagining', 'Creating a picture for each scene.', 'Your chosen style carries through the whole story.'],
    sam: ['Looking closer', 'Checking the subjects in your scene images.', 'Scene checks help the visuals stay on brief.'],
    voice: ['Giving it a voice', 'Turning your narration into spoken audio.', 'Your approved words become the voice of your video.'],
    assemble: ['Bringing it together', 'Joining your scenes and voice into one video.', 'The pieces of your story are coming together.'],
    qc: ['Checking the details', 'Checking the video’s sound, size, and playback.', 'The finished file needs to pass these checks.'],
    final: ['Finishing up', 'Waiting for the finished video.', 'Your preview will appear here when it’s ready.'],
    reconnecting: ['Reconnecting', 'Waiting for the studio to respond.', 'Your last known progress is shown below.']
  };
  var previewMeta = document.getElementById('previewMeta');
  var progressList = document.getElementById('progressList');
  var currentStep = document.getElementById('currentStep');
  var planList = document.getElementById('planList');
  var planEmpty = document.getElementById('planEmpty');
  var planCount = document.getElementById('planCount');
  var planHint = document.getElementById('planHint');
  var approvalNote = document.getElementById('approvalNote');
  var durationWarn = document.getElementById('durationWarn');
  var lockHint = document.getElementById('lockHint');
  var planProblem = document.getElementById('planProblem');
  var ackProduceBtn = document.getElementById('ackProduceBtn');
  var ackProduceBtnText = document.getElementById('ackProduceBtnText');
  var storyCheck = document.getElementById('storyCheck');
  var decisionList = document.getElementById('decisionList');
  var boardGrid = document.getElementById('boardGrid');
  var boardCount = document.getElementById('boardCount');
  var boardEmpty = document.getElementById('boardEmpty');
  var sceneEditor = document.getElementById('sceneEditor');

  var previewFormat = document.getElementById('previewFormat');
  var previewDuration = document.getElementById('previewDuration');
  var productionState = document.getElementById('productionState');
  var projectTitle = document.getElementById('projectTitle');
  var exportVideo = document.getElementById('exportVideo');
  var sceneInspectors = [];
  var currentStoryboardScenes = [];
  var selectedSceneIndex = 0;

  function sizeNarrationLine(area) {
    area.style.height = 'auto';
    area.style.height = Math.max(44, area.scrollHeight + 2) + 'px';
  }

  function sizeScriptEditor() {
    if (document.getElementById('scriptPane').hidden) return;
    planList.querySelectorAll('textarea').forEach(sizeNarrationLine);
  }
  window.addEventListener('resize', sizeScriptEditor);

  function activateEditorTab(name) {
    document.querySelectorAll('[data-editor-tab]').forEach(function (tab) {
      var active = tab.dataset.editorTab === name;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
    });
    if (name === 'script') requestAnimationFrame(sizeScriptEditor);
  }

  document.querySelectorAll('[data-editor-tab]').forEach(function (tab) {
    tab.addEventListener('click', function () { activateEditorTab(tab.dataset.editorTab); });
  });
  document.querySelectorAll('[role="tablist"]').forEach(function (list) {
    list.addEventListener('keydown', function (event) {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(event.key) === -1) return;
      var tabs = Array.from(list.querySelectorAll('[role="tab"]:not(:disabled)'));
      var index = tabs.indexOf(document.activeElement);
      if (index < 0) return;
      event.preventDefault();
      var next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
        (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click();
      tabs[next].focus();
    });
  });

  function updateProjectTitle() {
    var topic = topicInput.value.trim();
    if (topic) {
      projectTitle.textContent = topic;
      projectTitle.removeAttribute('aria-label');
    } else {
      projectTitle.textContent = '';
      projectTitle.setAttribute('aria-label', 'Untitled production');
    }
  }
  topicInput.addEventListener('input', updateProjectTitle);

  function selectScene(index, inspect) {
    if (!sceneInspectors[index]) return;
    selectedSceneIndex = index;
    if (sceneEditor) sceneEditor.replaceChildren(sceneInspectors[index]);
    document.querySelectorAll('#boardGrid .scene').forEach(function (card, i) {
      card.dataset.selected = String(i === index);
      var button = card.querySelector('.scene-select');
      if (button) button.setAttribute('aria-pressed', String(i === index));
    });
    if (inspect) {
      var card = document.querySelectorAll('#boardGrid .scene')[index];
      if (card) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      if (outputVideo.getAttribute('src') && outputVideo.readyState >= 1) {
        var start = currentStoryboardScenes.slice(0, index).reduce(function (total, scene) {
          return total + (typeof scene.duration === 'number' ? scene.duration : 0);
        }, 0);
        try { outputVideo.currentTime = start; } catch (error) { /* metadata not ready */ }
      }
    }
  }

  function renderPlannedSequence(lines, example, visuals) {
    renderStoryboard({ id: null, planned: true, example: !!example,
      scenes: lines.map(function (line, index) {
        return { index: index, narration: line, visual_prompt: (visuals || [])[index] || '', status: 'planned', image: null };
      }) });
  }

  var cancelBtn = document.getElementById('cancelBtn');
  var retryBtn = document.getElementById('retryBtn');
  var cancelHint = document.getElementById('cancelHint');

  var STAGES = [
    ['scenes', 'Pictures'], ['sam', 'Picture check'],
    ['voice', 'Voice'], ['assemble', 'Putting it together'],
    ['qc', 'Final check'], ['final', 'Your video']
  ];
  var PROD_TOTAL = STAGES.length;
  var STATE_NAMES = { waiting: 'Waiting', working: 'Working', done: 'Done', error: 'Error', interrupted: 'Interrupted', cancelled: 'Cancelled' };
  var LAST_JOB_KEY = 'reeldirector.lastJobId';

  function rememberJob(jobId) {
    currentJobId = jobId;
    try { localStorage.setItem(LAST_JOB_KEY, jobId); } catch (e) { /* private mode: restore skips */ }
  }

  function formatElapsed(job) {
    if (job && typeof job.elapsed_s === 'number') return ' · ' + job.elapsed_s + 's elapsed';
    return '';
  }
  var DIMS = { '9:16': [720, 1280], '16:9': [1280, 720] };
  var planLines = [];
  var planMode = null;
  var currentJobId = null;
  var scriptJobId = null;
  var productionRunning = false;
  var pendingDurationAck = false;

  STAGES.forEach(function (pair) {
    var li = document.createElement('li');
    li.id = 'progress-' + pair[0];
    li.dataset.state = 'waiting';
    var dot = document.createElement('span');
    dot.className = 'p-dot';
    dot.setAttribute('aria-hidden', 'true');
    var label = document.createElement('span');
    label.textContent = pair[1];
    li.appendChild(dot);
    li.appendChild(label);
    progressList.appendChild(li);
  });

  function selectedAspect() {
    var checked = document.querySelector('input[name="aspect"]:checked');
    return checked ? checked.value : '9:16';
  }

  function setPreviewAspect(aspect) {
    workspace.dataset.previewAspect = (aspect === '16:9') ? '16:9' : '9:16';
    previewFormat.textContent = aspect === '16:9' ? 'Wide 16:9' : 'Vertical 9:16';
  }

  function syncTargetLength() {
    var seconds = Number(durationInput.value);
    previewDuration.textContent = seconds > 0 ? seconds + 's target' : 'Auto length';
  }
  durationInput.addEventListener('input', syncTargetLength);

  document.querySelectorAll('input[name="aspect"]').forEach(function (radio) {
    radio.addEventListener('change', function () { setPreviewAspect(selectedAspect()); });
  });

  function setJobState(state, announce) {
    workspace.dataset.jobState = state;
    document.body.dataset.jobState = state;
    if (state !== 'planning' && state !== 'producing') stopThinking();
    var labels = { empty: 'Ready to plan', planning: 'Thinking…', 'plan-ready': 'Ready to produce', 'waiting-approval': 'Ready for your review', producing: 'Creating your video…', done: 'Your video is ready', error: 'Needs attention', cancelled: 'Production cancelled', interrupted: 'Production interrupted' };
    productionState.textContent = labels[state] || state;
    if (announce) stateAnnouncer.textContent = announce;
  }

  function escapeHtml(value) {
    var element = document.createElement('span');
    element.textContent = String(value == null ? '' : value);
    return element.innerHTML;
  }

  function showPreviewMessage(state, icon, html) {
    stopThinking();
    exportVideo.hidden = true;
    previewFrame.hidden = true;
    previewState.hidden = false;
    previewState.dataset.state = state;
    previewIcon.textContent = icon;
    previewText.innerHTML = html;
  }

  function showPreviewVideo() {
    stopThinking();
    previewState.hidden = true;
    previewFrame.hidden = false;
  }

  function setThinkingWord(word) {
    if (thinkingWord.textContent === word) return;
    thinkingWord.textContent = word;
    thinkingWord.classList.remove('thinking-word-enter');
    if (!motionPreference.matches) {
      void thinkingWord.offsetWidth;
      thinkingWord.classList.add('thinking-word-enter');
    }
  }

  function stopThinking() {
    clearInterval(thinkingTimer);
    thinkingTimer = null;
    thinkingPhase = null;
    thinkingPanel.hidden = true;
    previewState.setAttribute('aria-busy', 'false');
    previewText.closest('.preview-message').hidden = false;
    planBtn.setAttribute('aria-busy', 'false');
    produceBtn.setAttribute('aria-busy', 'false');
    delete previewState.dataset.thinkingPhase;
  }

  function syncElapsedTicker() {
    clearInterval(thinkingTimer);
    thinkingTimer = null;
    if (thinkingPhase !== 'planning' || document.hidden) return;
    var tick = function () {
      var elapsed = Math.max(0, Math.round((Date.now() - thinkingStarted) / 1000));
      var topic = topicInput.value.trim();
      var context = topic ? topic + ' · ' + elapsed + 's elapsed' : elapsed + 's elapsed';
      if (thinkingContext.textContent !== context) thinkingContext.textContent = context;
      thinkingContext.hidden = false;
      if (Date.now() - thinkingStarted > 45000) {
        thinkingNote.textContent = 'Still working on your plan. You can keep this tab open.';
      }
    };
    tick();
    thinkingTimer = setInterval(tick, 1000);
  }

  function showThinking(phase, job) {
    var message = thinkingMessages[phase] || thinkingMessages.preparing;
    exportVideo.hidden = true;
    previewFrame.hidden = true;
    previewState.hidden = false;
    previewState.dataset.state = 'thinking';
    previewState.dataset.thinkingPhase = phase;
    previewState.setAttribute('aria-busy', 'true');
    previewText.closest('.preview-message').hidden = true;
    thinkingPanel.hidden = false;
    planBtn.setAttribute('aria-busy', String(phase === 'planning'));
    produceBtn.setAttribute('aria-busy', String(phase !== 'planning'));
    if (thinkingPhase !== phase) {
      thinkingPhase = phase;
      thinkingStarted = Date.now();
      setThinkingWord(message[0]);
      thinkingDetail.textContent = message[1];
      thinkingNote.textContent = message[2];
      stateAnnouncer.textContent = message[0] + '. ' + message[1];
      syncElapsedTicker();
    }
    // Planning shows a static word plus the elapsed-seconds ticker
    // above (the ticker owns the context line). Production stages
    // and counts come only from the server's job record.
    if (phase === 'planning' && !job) return;
    var context = '';
    if (job) {
      context = stageCountText(phase, sceneCounts(job)).replace(/^ \(|\)$/g, '') + formatElapsed(job);
      context = context.replace(/^ · /, '');
    }
    if (thinkingContext.textContent !== context) thinkingContext.textContent = context;
    thinkingContext.hidden = !context;
  }

  function showProductionThinking(job) {
    var active = STAGES.find(function (pair) { return stageState(job, pair[0]) === 'working'; });
    var finished = STAGES.slice(0, -1).every(function (pair) { return stageState(job, pair[0]) === 'done'; });
    showThinking(active ? active[0] : finished ? 'final' : 'preparing', job);
  }

  document.addEventListener('visibilitychange', syncElapsedTicker);
  window.addEventListener('pagehide', function () { clearInterval(thinkingTimer); });
  window.addEventListener('pageshow', syncElapsedTicker);

  function addLog(message, type) {
    /* Details tab removed: the job log lives in job.json, not the UI. */
  }

  function showProblem(error, fix) {
    planProblem.textContent = error + ' ' + fix;
    planProblem.hidden = false;
    errorLine.textContent = error;
    errorLine.hidden = false;
    fixLine.textContent = fix;
    fixLine.hidden = false;
  }

  function clearProblem() {
    planProblem.hidden = true;
    errorLine.hidden = true;
    fixLine.hidden = true;
  }

  function setProgress(key, state) {
    var li = document.getElementById('progress-' + key);
    if (!li) return;
    li.dataset.state = state;
    li.setAttribute('aria-label', li.textContent + ': ' + (STATE_NAMES[state] || state));
  }

  function resetProgress() {
    STAGES.forEach(function (pair) { setProgress(pair[0], 'waiting'); });
    currentStep.textContent = 'Your studio is ready.';
  }

  function markProgressError() {
    STAGES.forEach(function (pair) {
      var li = document.getElementById('progress-' + pair[0]);
      if (li && li.dataset.state === 'working') setProgress(pair[0], 'error');
    });
  }

  function stageState(job, key) {
    if (key !== 'final') return (job.stages && job.stages[key]) || 'waiting';
    var status = job.status;
    if (status === 'done') return 'done';
    if (status === 'error') return 'error';
    if (status === 'cancelled') return 'cancelled';
    if (status === 'interrupted') return 'interrupted';
    return 'waiting';
  }

  function sceneCounts(job) {
    var scenes = (job && job.scenes) || [];
    return {
      total: scenes.length,
      images: scenes.filter(function (s) { return !!s.image; }).length,
      checked: scenes.filter(function (s) { return !!s.sam; }).length,
      voiced: scenes.filter(function (s) { return typeof s.duration === 'number'; }).length,
      assembled: scenes.filter(function (s) { return s.status === 'done'; }).length
    };
  }

  function stageCountText(key, counts) {
    if (!counts.total) return '';
    if (key === 'scenes') return ' (image ' + counts.images + ' of ' + counts.total + ')';
    if (key === 'sam') return ' (check ' + counts.checked + ' of ' + counts.total + ')';
    if (key === 'voice') return ' (voice ' + counts.voiced + ' of ' + counts.total + ')';
    if (key === 'assemble') return ' (scene ' + counts.assembled + ' of ' + counts.total + ')';
    return '';
  }

  function describeProgress(job) {
    var counts = sceneCounts(job);
    var suffix = formatElapsed(job);
    var workingKey = null;
    var doneCount = 0;
    STAGES.forEach(function (pair) {
      var state = stageState(job, pair[0]);
      if (state === 'done') doneCount += 1;
      else if (workingKey === null && (state === 'working' || state === 'waiting')) workingKey = pair[0];
    });
    if (workingKey !== null) {
      var label = '';
      STAGES.forEach(function (pair) { if (pair[0] === workingKey) label = pair[1]; });
      currentStep.textContent = 'Step ' + (doneCount + 1) + ' of ' + PROD_TOTAL + ': ' + label + stageCountText(workingKey, counts) + '.' + suffix;
    } else if (doneCount === PROD_TOTAL) {
      currentStep.textContent = 'All ' + PROD_TOTAL + ' steps finished.' + suffix;
    } else if (suffix) {
      currentStep.textContent = currentStep.textContent.replace(/ · .*s elapsed$/, '') + suffix;
    }
  }

  function wait(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function setHealth(state) {
    statusPill.classList.toggle('online', state === 'connected');
    statusPill.classList.toggle('offline', state !== 'connected');
    if (state === 'connected') {
      statusText.textContent = 'Connected';
    } else if (state === 'key') {
      statusText.textContent = 'Key configured';
    } else if (state === 'conn-failed') {
      statusText.textContent = 'Connection failed';
    } else {
      statusText.textContent = 'Preview only (no key)';
    }
    keyRow.hidden = (state === 'connected');
  }

  function connDetail(data) {
    if (!data) return '';
    var out = [];
    if (data.connection && data.connection.checked && data.connection.ok) {
      out.push('API online' + (data.connection.models != null ? ' (' + data.connection.models + ' models)' : '') + '.');
    } else if (data.connection && data.connection.checked && !data.connection.ok) {
      out.push('Connection check failed: ' + (data.connection.error || 'unknown error'));
    } else if (data.key && data.key.configured) {
      out.push('Key configured; connection not checked yet.');
    } else {
      out.push('No key: preview only.');
    }
    ['ffmpeg', 'ffprobe'].forEach(function (tool) {
      var info = data[tool];
      if (info && info.available) {
        out.push(tool + ' ok.');
      } else if (info) {
        out.push(tool + ' missing: ' + (info.error || 'not found'));
      }
    });
    return out.join(' ');
  }

  async function checkHealth() {
    try {
      var response = await fetch('/api/health');
      var data = await response.json();
      if (!data || data.ok !== true) {
        setHealth('demo');
        return;
      }
      var keyOn = !!(data.key && data.key.configured);
      var connOn = !!(data.connection && data.connection.checked && data.connection.ok);
      var connFailed = !!(keyOn && data.connection && data.connection.checked && !data.connection.ok);
      setHealth(connOn ? 'connected' : (connFailed ? 'conn-failed' : (keyOn ? 'key' : 'demo')));
      if (data.connection && !data.connection.ok && data.connection.error) {
        addLog('Status: ' + (keyOn ? 'key configured, ' : '') + 'connection failed: ' + data.connection.error, 'error');
      }
    } catch (error) {
      setHealth('demo');
    }
  }

  function markPicked(grid, id) {
    grid.querySelectorAll('button').forEach(function (btn) {
      btn.setAttribute('aria-pressed', btn.dataset.pickId === id ? 'true' : 'false');
    });
  }

  function updateLookSummary() {
    var styleName = null;
    var colorName = null;
    styleChoices.forEach(function (preset) {
      if (preset.id === selectedStyle) styleName = preset.name;
    });
    colorChoices.forEach(function (theme) {
      if (theme.id === selectedColor) colorName = theme.name;
    });
    if (styleName && colorName) {
      lookSummary.textContent = styleName + ', ' + colorName;
    }
  }

  function setStylePick(id) {
    selectedStyle = id;
    markPicked(styleGrid, id);
    updateLookSummary();
  }

  function setColorPick(id) {
    selectedColor = id;
    markPicked(colorGrid, id);
    updateLookSummary();
  }

  function currentLook() {
    var look = {};
    if (selectedStyle) look.style = selectedStyle;
    if (selectedColor) look.color = selectedColor;
    var custom = customInput.value.trim();
    if (custom) look.style_custom = custom;
    return look;
  }

  async function loadStyleOptions() {
    try {
      var response = await fetch('/api/style');
      var data = await response.json();
      if (!data || !Array.isArray(data.styles) || !Array.isArray(data.colors)) throw new Error('bad options');
      styleChoices = data.styles;
      colorChoices = data.colors;
      styleGrid.innerHTML = '';
      data.styles.forEach(function (preset) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pick-card';
        btn.dataset.pickId = preset.id;
        btn.setAttribute('aria-pressed', 'false');
        btn.title = preset.blurb || preset.name;
        btn.setAttribute('aria-label',
          preset.name + ': ' + (preset.blurb || preset.name));
        var name = document.createElement('strong');
        name.textContent = preset.name;
        btn.appendChild(name);
        btn.addEventListener('click', function () { setStylePick(preset.id); });
        styleGrid.appendChild(btn);
      });
      colorGrid.innerHTML = '';
      data.colors.forEach(function (theme) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'swatch-card';
        btn.dataset.pickId = theme.id;
        btn.setAttribute('aria-pressed', 'false');
        var dot = document.createElement('span');
        dot.className = 'swatch-dot';
        dot.style.background = theme.swatch || '#BCC0C4';
        dot.setAttribute('aria-hidden', 'true');
        var name = document.createElement('strong');
        name.textContent = theme.name;
        btn.appendChild(dot);
        btn.appendChild(name);
        btn.title = theme.blurb || theme.name;
        btn.setAttribute('aria-label',
          theme.name + ': ' + (theme.blurb || theme.name));
        btn.addEventListener('click', function () { setColorPick(theme.id); });
        colorGrid.appendChild(btn);
      });
      setStylePick(data.default_style || (data.styles[0] && data.styles[0].id));
      setColorPick(data.default_color || (data.colors[0] && data.colors[0].id));
    } catch (error) {
      styleGrid.innerHTML = '<p class="pick-error">Style options did not load. Reload the page.</p>';
      addLog('Style and color options did not load; reload the page.', 'error');
    }
  }

  function renderPlanEditor(lines, visuals, mode) {
    planLines = lines.slice();
    planMode = mode;
    planList.innerHTML = '';
    var suffix = mode === 'demo-preview' ? 'example, not rendered' : 'live, editable until approved';
    lines.forEach(function (text, index) {
      var item = document.createElement('li');
      item.className = 'plan-line';
      var number = document.createElement('span');
      number.className = 'line-number';
      number.textContent = String(index + 1);
      var fields = document.createElement('div');
      fields.className = 'plan-fields';
      var lineLabel = document.createElement('label');
      lineLabel.textContent = 'Narration';
      var area = document.createElement('textarea');
      area.value = text;
      area.dataset.planLine = String(index);
      area.setAttribute('aria-label', 'Scene ' + (index + 1) + ' narration');
      area.addEventListener('input', function () {
        sizeNarrationLine(area);
        if (workspace.dataset.jobState === 'waiting-approval') {
          var edited = readEditedPlan();
          renderPlannedSequence(edited.lines, false, edited.visuals);
        }
      });
      if (mode === 'demo-preview') area.disabled = true;
      fields.appendChild(lineLabel);
      fields.appendChild(area);
      if (mode !== 'demo-preview') {
        var visualLabel = document.createElement('label');
        visualLabel.textContent = 'Visual';
        var visual = document.createElement('textarea');
        visual.value = (visuals && visuals[index]) || '';
        visual.dataset.planVisual = String(index);
        visual.addEventListener('input', function () {
          sizeNarrationLine(visual);
          if (workspace.dataset.jobState === 'waiting-approval') {
            var edited = readEditedPlan();
            renderPlannedSequence(edited.lines, false, edited.visuals);
          }
        });
        visual.setAttribute('aria-label', 'Scene ' + (index + 1) + ' visual prompt');
        fields.appendChild(visualLabel);
        fields.appendChild(visual);
      }
      item.appendChild(number);
      item.appendChild(fields);
      planList.appendChild(item);
    });
    planEmpty.hidden = true;
    planList.hidden = false;
    planCount.textContent = lines.length + (lines.length === 1 ? ' scene' : ' scenes');
    var live = mode !== 'demo-preview';
    produceBtn.hidden = !live;
    planHint.hidden = !live;
    activateEditorTab('script');
    renderPlannedSequence(lines, !live, visuals);
  }

  function readEditedPlan() {
    var lines = [];
    var visuals = [];
    planList.querySelectorAll('textarea[data-plan-line]').forEach(function (area) {
      var text = area.value.trim();
      if (text) {
        lines.push(text);
        var visual = planList.querySelector('textarea[data-plan-visual="' + area.dataset.planLine + '"]');
        visuals.push(visual ? visual.value.trim() : '');
      }
    });
    return { lines: lines, visuals: visuals };
  }

  function setPlanLocked(locked) {
    planList.querySelectorAll('textarea').forEach(function (area) {
      area.disabled = locked || planMode === 'demo-preview';
    });
    productionRunning = locked;
    planHint.hidden = locked || planList.hidden;
    lockHint.hidden = !locked;
    produceBtn.disabled = locked;
    ackProduceBtn.hidden = locked || !pendingDurationAck;
    planForm.querySelectorAll('input, textarea, button').forEach(function (control) { control.disabled = locked; });
    document.querySelectorAll('#boardGrid .scene-body textarea, #boardGrid .scene-body button').forEach(function (control) {
      if (locked) {
        if (control.dataset.lockRestore === undefined) control.dataset.lockRestore = String(control.disabled);
        control.disabled = true;
      } else if (control.dataset.lockRestore !== undefined) {
        control.disabled = control.dataset.lockRestore === 'true';
        delete control.dataset.lockRestore;
      }
    });
  }

  var boardSig = '';

  function sceneSig(scenes) {
    return JSON.stringify((scenes || []).map(function (s) {
      return [s.image, s.status, s.duration, s.dirty, s.dirty_reason,
              s.provider, s.fallback,
              s.sam ? [s.sam.matches, s.sam.concept, s.sam.warning] : null,
              s.revision_status, s.before_narration, s.after_narration];
    }));
  }

  function editingScene() {
    var active = document.activeElement;
    return !!(active && active.dataset && (active.dataset.sceneEdit !== undefined || active.dataset.planLine !== undefined || active.dataset.planVisual !== undefined));
  }

  function directorUnmetByScene(job) {
    var map = {};
    var list = (job && job.director && job.director.unmet_criteria) || [];
    list.forEach(function (u) {
      (map[u.scene_id] = map[u.scene_id] || []).push(u);
    });
    return map;
  }

  function renderStoryboard(job) {
    var scenes = (job && job.scenes) || [];
    var jobId = job && job.id;
    var unmetByScene = directorUnmetByScene(job);
    var locked = job.status === 'running' || job.status === 'queued';
    var planned = !!job.planned || job.status === 'waiting_approval' || job.status === 'approved';
    boardGrid.innerHTML = '';
    sceneInspectors = [];
    currentStoryboardScenes = scenes;
    boardEmpty.style.display = scenes.length ? 'none' : 'block';
    scenes.forEach(function (scene) {
      var card = document.createElement('article');
      card.className = 'scene';
      var clip = document.createElement('button');
      clip.type = 'button';
      clip.className = 'scene-select';
      clip.setAttribute('aria-label', 'Inspect scene ' + (scene.index + 1));
      clip.setAttribute('aria-pressed', 'false');
      clip.addEventListener('click', function () { selectScene(scene.index, true); });
      if (scene.image) {
        var art = document.createElement('img');
        art.src = scene.image;
        art.alt = 'Scene ' + (scene.index + 1) + ' live render';
        art.width = 720;
        art.height = 1280;
        art.loading = 'lazy';
        art.decoding = 'async';
        clip.appendChild(art);
      } else {
        var hold = document.createElement('div');
        hold.className = 'scene-placeholder';
        hold.textContent = planned ? 'Awaiting image' : 'Generating image';
        clip.appendChild(hold);
      }
      var clipMeta = document.createElement('span');
      clipMeta.className = 'scene-clip-meta';
      var clipNumber = document.createElement('span');
      clipNumber.textContent = 'Scene ' + (scene.index + 1);
      var clipDuration = document.createElement('span');
      clipDuration.textContent = typeof scene.duration === 'number' ? scene.duration.toFixed(1) + 's' : (planned ? 'Planned' : 'In progress');
      clipMeta.appendChild(clipNumber);
      clipMeta.appendChild(clipDuration);
      clip.appendChild(clipMeta);
      var clipCaption = document.createElement('span');
      clipCaption.className = 'scene-clip-caption';
      clipCaption.textContent = scene.narration || 'Scene narration pending';
      clip.appendChild(clipCaption);
      card.appendChild(clip);
      var body = document.createElement('div');
      body.className = 'scene-body';
      var title = document.createElement('p');
      title.textContent = 'Scene ' + (scene.index + 1);
      body.appendChild(title);
      var area = document.createElement('textarea');
      area.value = scene.narration || '';
      area.name = 'scene-narration-' + scene.index;
      area.dataset.sceneEdit = String(scene.index);
      area.setAttribute('aria-label', 'Scene ' + (scene.index + 1) + ' narration');
      area.disabled = planned || locked;
      body.appendChild(area);
      if (scene.visual_prompt) {
        var visual = document.createElement('p');
        visual.className = 'scene-meta';
        visual.textContent = 'Visual: ' + scene.visual_prompt;
        body.appendChild(visual);
      }
      var meta = document.createElement('p');
      meta.className = 'scene-meta';
      var bits = [];
      if (planned) {
        bits.push(job.example ? 'Script preview only. No media generated.' : 'Scene planned. Media has not been generated yet.');
      } else {
        bits.push('Length: ' + (typeof scene.duration === 'number' ? scene.duration.toFixed(1) + 's' : '—'));
        bits.push('Image: ' + (scene.provider || 'muse-image-1.0'));
        bits.push('Fallback: ' + (scene.fallback || 'none (Meta-only, fail-hard)'));
      }
      if (scene.style_name || scene.color_name) bits.push('Look: ' + (scene.style_name || 'Flat vector') + ' · ' + (scene.color_name || 'Facebook blue'));
      if (scene.sam && scene.sam.matches != null) {
        bits.push('SAM: ' + scene.sam.matches + ' match' + (scene.sam.matches === 1 ? '' : 'es') +
                  (scene.sam.concept ? ' (' + scene.sam.concept + ')' : ''));
      }
      if (scene.sam && scene.sam.warning) bits.push('SAM note: ' + scene.sam.warning);
      meta.textContent = bits.join(' · ');
      body.appendChild(meta);
      var status = document.createElement('span');
      status.className = 'scene-status';
      var st = scene.dirty ? 'dirty' : (scene.status || 'waiting');
      status.dataset.state = (st === 'done' || st === 'error' || st === 'dirty' || st === 'interrupted' || st === 'cancelled') ? st : 'working';
      if (st === 'planned') status.dataset.state = 'waiting';
      if (['waiting', 'image ready', 'checked', 'voiced'].indexOf(st) !== -1) status.dataset.state = 'working';
      var dot = document.createElement('span');
      dot.className = 'state-dot';
      status.appendChild(dot);
      var label = document.createElement('span');
      label.textContent = scene.dirty ? 'Needs regeneration' : (st === 'planned' ? 'Planned' : st);
      status.appendChild(label);
      body.appendChild(status);
      if (scene.dirty && scene.dirty_reason) {
        var dirty = document.createElement('p');
        dirty.className = 'scene-dirty';
        dirty.textContent = scene.dirty_reason;
        body.appendChild(dirty);
      }
      if (scene.revision_status && scene.revision_status !== 'planned') {
        var rev = document.createElement('p');
        rev.className = 'director-line';
        var crit = (scene.revision_criteria || []).join(', ') || 'director review';
        rev.textContent = 'Story check: ' + scene.revision_status + ' (' + crit + ').' +
          (scene.before_narration ? ' Narration was: "' + scene.before_narration + '" now: "' + (scene.after_narration || scene.narration) + '".' : '') +
          (scene.before_visual ? ' Visual was: "' + scene.before_visual + '" now: "' + (scene.after_visual || '') + '".' : '');
        body.appendChild(rev);
      }
      (unmetByScene[scene.scene_id] || []).forEach(function (u) {
        var un = document.createElement('p');
        un.className = 'director-unmet';
        un.textContent = 'Story check unresolved (' + u.criterion + '): ' + u.detail + ' The story check did not pass for this scene.';
        body.appendChild(un);
      });
      var row = document.createElement('div');
      row.className = 'scene-row';
      if (planned) {
        var editScript = document.createElement('button');
        editScript.type = 'button';
        editScript.className = 'scene-btn';
        editScript.textContent = 'Edit in script';
        editScript.disabled = !!job.example;
        editScript.addEventListener('click', function () {
          activateEditorTab('script');
          var editor = planList.querySelector('textarea[data-plan-line="' + scene.index + '"]');
          if (editor) editor.focus();
        });
        row.appendChild(editScript);
      } else {
      var save = document.createElement('button');
      save.type = 'button';
      save.className = 'scene-btn';
      save.textContent = 'Save narration';
      save.disabled = locked;
      save.addEventListener('click', function () { saveScene(jobId, scene.index, area.value, save); });
      var regen = document.createElement('button');
      regen.type = 'button';
      regen.className = 'scene-btn';
      regen.textContent = 'Regenerate image';
      regen.disabled = locked;
      regen.addEventListener('click', function () { regenScene(jobId, scene.index, regen); });
      row.appendChild(save);
      row.appendChild(regen);
      }
      body.appendChild(row);
      sceneInspectors.push(body);
      boardGrid.appendChild(card);
    });
    boardCount.textContent = scenes.length ? scenes.length + (scenes.length === 1 ? ' scene' : ' scenes') : '';
    if (scenes.length) selectScene(Math.min(selectedSceneIndex, scenes.length - 1), false);
    boardSig = sceneSig(scenes);
  }

  function renderExampleBoard(lines) {
    renderPlannedSequence(lines, true);
  }

  function addDecision(icon, cls, text) {
    var li = document.createElement('li');
    var glyph = document.createElement('span');
    glyph.className = 'material-symbols-outlined ' + cls;
    glyph.setAttribute('aria-hidden', 'true');
    glyph.textContent = icon;
    var copy = document.createElement('span');
    copy.textContent = text;
    li.appendChild(glyph);
    li.appendChild(copy);
    decisionList.appendChild(li);
  }

  function renderStoryCheck(job, failed) {
    decisionList.innerHTML = '';
    var log = job && job.director;
    if (log && log.brief) {
      addDecision('info', 'info', 'Brief: "' + log.brief.topic + '" for ' + log.brief.audience + ', target ' + log.brief.target_duration_s + 's.');
    } else if (job && job.brief && job.brief.topic) {
      addDecision('info', 'info', 'Brief: "' + job.brief.topic + '", format ' + (job.brief.aspect_ratio || job.aspect_ratio || selectedAspect()) + '.');
    }
    var attempts = (log && log.attempts) || [];
    var grouped = (log && log.grouped_attempts) || null;
    if (grouped && grouped.length) {
      grouped.forEach(function (g) {
        var cycles = (g.cycles || []).join(', ');
        var what = g.status === 'narration-revised' ? 'narration revised'
          : g.status === 'visual-revised' ? 'visual revised'
          : g.status === 'unchanged' ? 'checked, unchanged'
          : g.status === 'failed' ? 'revision failed'
          : 'revised';
        if (g.status === 'failed') {
          addDecision('error', 'bad', 'Scene ' + g.scene_id + ' ' + g.criterion + ' (cycles ' + cycles + '): revision failed.');
        } else {
          addDecision('check_circle', 'ok', 'Scene ' + g.scene_id + ' ' + g.criterion + ' (cycles ' + cycles + '): ' + what + '.');
        }
      });
    } else {
      attempts.forEach(function (a) {
        var what = a.status === 'narration-revised' ? 'narration revised'
          : a.status === 'visual-revised' ? 'visual revised'
          : a.status === 'unchanged' ? 'checked, unchanged'
          : a.status;
        if (what === 'revised' || what === 'narration revised' || what === 'visual revised') {
          addDecision('check_circle', 'ok', 'Scene ' + a.scene_id + ' ' + what + ' (' + (a.criteria || []).join(', ') + ').');
        } else if (a.error) {
          addDecision('error', 'bad', 'Scene ' + a.scene_id + ' could not be revised: ' + a.error);
        }
      });
    }
    var unmet = (log && log.unmet_criteria) || [];
    unmet.forEach(function (u) {
      addDecision('warning', 'warn', 'Unresolved: ' + u.scene_id + ' still fails ' + u.criterion + '. Not passed.');
    });
    if (log && log.editorial_pass) {
      addDecision('check_circle', 'ok', 'Story check passed (' + attempts.length + ' revision(s)).');
    } else if (log && !log.editorial_pass) {
      addDecision('warning', 'warn', 'Story check did not fully pass.');
    }
    if (job && (job.style_name || job.color_name)) {
      addDecision('palette', 'info', 'Look: ' + (job.style_name || 'Flat vector') + ' · ' + (job.color_name || 'Facebook blue') + '.');
    }
    var gate = job && job.status;
    if (gate === 'waiting_approval') {
      addDecision('hourglass_empty', 'warn', 'Approval gate: waiting for your approval. No images, no voice, no video yet.');
    } else if (gate === 'running' || gate === 'queued') {
      addDecision('progress_activity', 'info', 'Approval gate: approved. Production running with no further clicks needed.');
    } else if (gate === 'done') {
      addDecision('check_circle', 'ok', 'Approval gate: approved, production finished on its own.');
    } else if (gate === 'error' || gate === 'cancelled' || gate === 'interrupted') {
      addDecision('warning', 'warn', 'Approval gate: approved, production ended ' + gate + '.');
    }
    var changeNote = (job && job.revision_note) || (job && job.qc && job.qc.revision_note);
    if (changeNote) {
      addDecision('edit_note', 'info', 'Script revise note: ' + changeNote);
    } else if (log) {
      addDecision('edit_note', 'info', 'Script revise note: none recorded (script unchanged by revise).');
    }
    var qc = job && job.qc;
    if (qc) {
      var dims = qc.video ? qc.video.split(' ')[0] : null;
      var expected = qc.expected_dimensions;
      if (dims && expected) {
        if (dims === expected) {
          addDecision('check_circle', 'ok', 'Output ' + dims + ' matches ' + (qc.aspect_ratio || 'selected format') + '.');
        } else {
          addDecision('error', 'bad', 'Output ' + dims + ' does not match ' + expected + ' (' + (qc.aspect_ratio || 'selected format') + ').');
        }
      }
      var verdict = qc.passed ? 'QC passed' : 'QC failed';
      var why = qc.error || (qc.technical ?
        'duration ' + (qc.technical.duration_ok ? 'ok' : 'bad') +
        ', resolution ' + (qc.technical.resolution_ok ? 'ok' : 'bad') +
        ', audio ' + (qc.technical.audio_ok ? 'ok' : 'bad') +
        ', audible ' + (qc.technical.audible_ok ? 'ok' : 'bad') : 'no technical detail');
      addDecision(qc.passed ? 'check_circle' : 'error', qc.passed ? 'ok' : 'bad', verdict + ': ' + why + '.');
      var runDecisions = qc.decisions || [];
      runDecisions.slice(0, 8).forEach(function (d) {
        if (d && d.decision) addDecision(d.retried ? 'autorenew' : 'info', d.retried ? 'warn' : 'info', d.decision);
      });
      if (runDecisions.length > 8) {
        addDecision('info', 'info', (runDecisions.length - 8) + ' more decision(s) in the job log.');
      }
    }
    if (failed) {
      addDecision('error', 'bad', 'Production failed: ' + failed);
    }
    if (storyCheck) storyCheck.hidden = !decisionList.children.length;
  }

  function formatDims(job) {
    var aspect = (job && (job.aspect_ratio || (job.brief && job.brief.aspect_ratio))) || selectedAspect();
    var dims = DIMS[aspect] || DIMS['9:16'];
    var w = (job && job.width) || dims[0];
    var h = (job && job.height) || dims[1];
    var name = aspect === '9:16' ? 'Vertical 9:16' : 'Wide 16:9';
    return { aspect: aspect, label: name + ' · ' + w + '×' + h + ' MP4' };
  }

  async function saveScene(jobId, index, narration, btn) {
    if (!jobId) return;
    btn.disabled = true;
    try {
      var response = await fetch('/api/job/' + jobId + '/scene/' + index, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ narration: narration })
      });
      var data = await response.json();
      if (!response.ok) throw new Error((data && data.error) || 'save failed');
      addLog('Scene ' + (index + 1) + ': narration ' + (data.plan || 'saved') + '.', 'success');
      if (data.scene && data.scene.dirty_reason) addLog('Scene ' + (index + 1) + ' dirty: ' + data.scene.dirty_reason);
      refreshBoard(jobId);
    } catch (error) {
      addLog('Scene ' + (index + 1) + ' save failed: ' + error.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  async function regenScene(jobId, index, btn) {
    if (!jobId) return;
    btn.disabled = true;
    btn.textContent = 'Regenerating';
    try {
      var response = await fetch('/api/job/' + jobId + '/scene/' + index + '/regenerate', { method: 'POST' });
      var data = await response.json();
      if (!response.ok) throw new Error((data && data.error) || 'regenerate failed');
      addLog('Scene ' + (index + 1) + ': image regenerated (this scene only).', 'success');
      if (data.note) addLog(data.note);
      refreshBoard(jobId);
    } catch (error) {
      addLog('Scene ' + (index + 1) + ' regenerate failed: ' + error.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Regenerate image';
    }
  }

  async function refreshBoard(jobId) {
    try {
      var poll = await fetch('/api/job/' + jobId);
      var job = await poll.json();
      if (job && job.scenes) renderStoryboard(job);
    } catch (error) {
      addLog('Storyboard refresh failed: ' + error.message, 'error');
    }
  }

  sampleBtn.addEventListener('click', async function () {
    try {
      var response = await fetch('/api/sample');
      var data = await response.json();
      if (data && data.topic) { topicInput.value = data.topic; updateProjectTitle(); }
    } catch (error) {
      showProblem('The sample did not load.', 'Type a topic by hand instead.');
    }
  });

  keyBtn.addEventListener('click', async function () {
    var key = keyInput.value.trim();
    if (!key) {
      showProblem('No key was entered.', 'Paste your key, then save.');
      return;
    }
    try {
      var response = await fetch('/api/key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key })
      });
      var data = await response.json();
      if (!response.ok || !data || data.ok !== true) throw new Error('rejected');
      keyInput.value = '';
      clearProblem();
      addLog('Key saved for this session.', 'success');
      checkHealth();
    } catch (error) {
      showProblem('The key was not saved.', 'Check the key and try again.');
    }
  });

  function renderApproval(approval) {
    var lines = approval.script.map(function (line) { return String(line); });
    var visuals = (approval.visuals || []).map(function (line) { return String(line); });
    scriptJobId = approval.job_id || null;
    if (scriptJobId) rememberJob(scriptJobId);
    pendingDurationAck = false;
    ackProduceBtn.hidden = true;
    if (approval.style) setStylePick(approval.style);
    if (approval.color) setColorPick(approval.color);
    if (approval.style_custom != null) customInput.value = approval.style_custom;
    renderPlanEditor(lines, visuals, 'live');
    setPlanLocked(false);
    var brief = approval.brief || {};
    var est = approval.duration_estimate || {};
    var bits = [];
    bits.push('Topic: "' + (brief.topic || '') + '"');
    if (brief.audience) bits.push('Audience: ' + brief.audience);
    if (brief.target_duration_s) bits.push('Target: ' + brief.target_duration_s + 's');
    if (est.estimated_total_s != null) bits.push('Estimated: ' + est.estimated_total_s + 's narration');
    approvalNote.textContent = 'Waiting for your approval. ' + bits.join(' · ') + '. Script and director review are done; no images, no voice, no video exist yet.';
    approvalNote.hidden = false;
    if (est.preflight && est.preflight.warning) {
      durationWarn.innerHTML = '';
      var strong = document.createElement('strong');
      strong.textContent = 'Duration warning. ';
      durationWarn.appendChild(strong);
      durationWarn.appendChild(document.createTextNode(est.preflight.warning + '. ' + (est.preflight.suggestion || '') + '.'));
      durationWarn.hidden = false;
      ackProduceBtnText.textContent = 'Approve ' + est.preflight.estimated_total_s + 's & produce';
    } else {
      durationWarn.hidden = true;
    }
    renderStoryCheck({ status: 'waiting_approval', brief: brief, director: approval.director, revision_note: approval.revision_note, qc: null }, null);
    var unmet = approval.unmet_criteria || [];
    unmet.forEach(function (u) {
      addLog('Unmet after review (' + u.scene_id + ' / ' + u.criterion + '): ' + u.detail);
    });
    if (approval.editorial_pass) {
      addLog('Story check passed.', 'success');
    } else if (unmet.length) {
      addLog('Story check surfaced ' + unmet.length + ' unmet criteria (shown above); production can still run.', 'warn');
    }
    if (approval.revision_note) addLog('Revision note: ' + approval.revision_note);
    addLog('Scene plan ready: ' + lines.length + ' lines with visuals. Review and edit, then approve.', 'success');
    showPreviewMessage('plan', 'rate_review', '<strong>Your story is taking shape.</strong><br>Review the script and visuals, make it yours, then approve production.');
    setJobState('waiting-approval', 'Waiting for approval.');
    currentStep.textContent = 'Waiting for approval: review the script and visuals, then approve.';
  }

  planForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    var topic = topicInput.value.trim();
    if (!topic) {
      showProblem('A topic is needed first.', 'Type a topic in the brief.');
      topicInput.focus();
      return;
    }
    var audience = audienceInput.value.trim();
    var targetRaw = durationInput.value;
    var target = null;
    if (targetRaw !== '' && targetRaw != null) {
      target = Number(targetRaw);
      if (!isFinite(target) || target <= 0) {
        showProblem('The target length must be a positive number of seconds.', 'Fix it in the brief, or leave it empty for auto.');
        durationInput.focus();
        return;
      }
    }
    var look = currentLook();
    var aspect = selectedAspect();
    planBtn.disabled = true;
    planBtnText.textContent = 'Thinking…';
    produceBtn.hidden = true;
    ackProduceBtn.hidden = true;
    pendingDurationAck = false;
    retryBtn.hidden = true;
    approvalNote.hidden = true;
    durationWarn.hidden = true;
    updateProjectTitle();
    clearProblem();
    setJobState('planning', 'Thinking about your scene plan.');
    showThinking('planning');
    currentStep.textContent = 'Thinking through your script and visuals. You’ll review them next.';
    try {
      var planBody = { topic: topic, aspect_ratio: aspect };
      if (audience) planBody.audience = audience;
      if (target != null) planBody.target_duration_s = target;
      if (look.style) planBody.style = look.style;
      if (look.color) planBody.color = look.color;
      if (look.style_custom) planBody.style_custom = look.style_custom;
      var response = await fetch('/api/script', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(planBody)
      });
      var data = await response.json();
      if (!response.ok || !data || !Array.isArray(data.script) || !data.script.length) {
        throw new Error((data && data.error) || 'script failed');
      }
      if (data.mode === 'demo-preview') {
        var lines = data.script.map(function (line) { return String(line); });
        scriptJobId = null;
        approvalNote.hidden = true;
        durationWarn.hidden = true;
        renderPlanEditor(lines, [], data.mode);
        addLog(data.note || 'Preview only. No video without a key.', 'warn');
        showProblem('This is a preview only.', 'Paste your key in the brief for a real video.');
        showPreviewMessage('empty', 'movie', '<strong>Preview only.</strong><br>Add your key to produce a real video.');
        renderExampleBoard(lines);
        setJobState('empty', 'Preview only. No video without a key.');
        currentStep.textContent = 'Not started.';
      } else if (data.state === 'waiting_approval') {
        renderApproval(data);
      } else {
        throw new Error('unexpected script response');
      }
    } catch (error) {
      var message = String((error && error.message) || 'planning failed');
      showProblem('The plan could not be made.', 'Check the technical details, then try again.');
      showPreviewMessage('error', 'error', '<strong>Planning failed.</strong><br>' + escapeHtml(message));
      setJobState('error', 'Planning failed.');
      addLog('Planning stopped: ' + message, 'error');
      currentStep.textContent = 'Planning failed.';
    } finally {
      planBtn.disabled = false;
      planBtnText.textContent = 'Plan scenes';
    }
  });

  function markProgressLeft(state) {
    STAGES.forEach(function (pair) {
      var li = document.getElementById('progress-' + pair[0]);
      if (li && li.dataset.state === 'working') setProgress(pair[0], state);
    });
  }

  function syncProgressPills(job, seen) {
    STAGES.forEach(function (pair) {
      var state = stageState(job, pair[0]);
      if (state && (!seen || seen[pair[0]] !== state)) {
        if (seen) seen[pair[0]] = state;
        setProgress(pair[0], state);
      }
    });
  }

  function showFinishedJob(job, fallbackAspect) {
    STAGES.forEach(function (pair) { setProgress(pair[0], 'done'); });
    currentStep.textContent = 'All ' + PROD_TOTAL + ' steps finished.' + formatElapsed(job);
    planList.hidden = true;
    approvalNote.hidden = true;
    durationWarn.hidden = true;
    planHint.hidden = true;
    ackProduceBtn.hidden = true;
    produceBtn.hidden = true;
    planEmpty.hidden = false;
    planEmpty.textContent = 'Production complete. Tap a scene below to edit it, or download your video.';
    setJobState('done', 'Video ready.');
    setPreviewAspect(job.aspect_ratio || fallbackAspect || '9:16');
    outputVideo.src = job.video;
    exportVideo.href = job.video;
    exportVideo.hidden = false;
    if (job.qc && typeof job.qc.duration === 'number') previewDuration.textContent = job.qc.duration.toFixed(1) + 's finished';
    showPreviewVideo();
    var info = formatDims(job);
    var metaBits = [info.label];
    if (job.qc && typeof job.qc.duration === 'number') metaBits.push(job.qc.duration.toFixed(1) + 's');
    if (job.qc && job.qc.audio) metaBits.push('voice audible');
    if (job.style_name || job.color_name) metaBits.push((job.style_name || 'Flat vector') + ' · ' + (job.color_name || 'Facebook blue'));
    previewMeta.innerHTML = '';
    var dl = document.createElement('a');
    dl.href = job.video;
    dl.setAttribute('download', '');
    dl.textContent = 'Download video';
    previewMeta.appendChild(dl);
    var rest = document.createElement('span');
    rest.textContent = '  ' + metaBits.join(' · ') + '.';
    previewMeta.appendChild(rest);
    previewMeta.hidden = false;
    renderStoryboard(job);
    renderStoryCheck(job, null);
    if (job.video_stale) addLog('Note: a scene changed after this video was built; the video is stale for that scene.');
    if (job.revision_note) addLog('Revision note: ' + job.revision_note);
    (job.qc && job.qc.decisions || []).forEach(function (entry) {
      addLog('Decision: ' + entry.decision);
    });
    addLog('Video ready in the preview.', 'success');
    retryBtn.hidden = true;
    setJobState('done', 'Video ready.');
  }

  function showFailedJob(job) {
    markProgressError();
    STAGES.forEach(function (pair) {
      if (stageState(job, pair[0]) === 'error') setProgress(pair[0], 'error');
    });
    if (job.scenes && job.scenes.length) renderStoryboard(job);
    renderStoryCheck(job, job.error || 'production failed');
    currentStep.textContent = 'Production failed.' + formatElapsed(job);
    showPreviewMessage('error', 'error', '<strong>Production failed.</strong><br>' + escapeHtml(job.error || 'production failed') + '<br>Retry re-renders from the stored plan.');
    addLog('Production stopped: ' + (job.error || 'production failed'), 'error');
    retryBtn.hidden = false;
    setJobState('error', 'Production failed.');
  }

  function showCancelledJob(job) {
    markProgressLeft('cancelled');
    STAGES.forEach(function (pair) {
      if (stageState(job, pair[0]) === 'cancelled') setProgress(pair[0], 'cancelled');
    });
    if (job.scenes && job.scenes.length) renderStoryboard(job);
    renderStoryCheck(job, job.error || 'production cancelled');
    currentStep.textContent = 'Production cancelled.' + formatElapsed(job);
    showPreviewMessage('error', 'cancel', '<strong>Production cancelled.</strong><br>' + escapeHtml(job.error || 'Stopped at a stage boundary.') + '<br>Retry re-renders from the stored plan.');
    addLog('Production cancelled: ' + (job.error || 'stopped at a stage boundary'), 'error');
    retryBtn.hidden = false;
    setJobState('cancelled', 'Production cancelled.');
  }

  function showInterruptedJob(job) {
    markProgressLeft('interrupted');
    STAGES.forEach(function (pair) {
      if (stageState(job, pair[0]) === 'interrupted') setProgress(pair[0], 'interrupted');
    });
    if (job.scenes && job.scenes.length) renderStoryboard(job);
    renderStoryCheck(job, job.interrupt_reason || 'production interrupted');
    currentStep.textContent = 'Production interrupted by a server restart.' + formatElapsed(job);
    var kept = (job.preserved && job.preserved.length) ? job.preserved.join(', ') : 'no finished files';
    showPreviewMessage('error', 'warning', '<strong>Interrupted by a server restart.</strong><br>' + escapeHtml(job.interrupt_reason || '') + '<br>Kept on disk: ' + escapeHtml(kept) + '.<br>' + escapeHtml(job.must_rerun || 'Retry re-renders from the stored plan.'));
    addLog('Production interrupted: ' + (job.interrupt_reason || 'server restarted mid-stage'), 'error');
    addLog('Kept on disk: ' + kept + '.');
    if (job.must_rerun) addLog(job.must_rerun);
    retryBtn.hidden = false;
    setJobState('interrupted', 'Production interrupted.');
  }

  async function followJob(jobId, fallbackAspect) {
    rememberJob(jobId);
    showThinking('preparing');
    cancelBtn.hidden = false;
    cancelHint.hidden = false;
    retryBtn.hidden = true;
    var seen = {};
    var misses = 0;
    try {
      while (true) {
        await wait(1000);
        var poll = null;
        try {
          poll = await fetch('/api/job/' + jobId);
        } catch (fetchError) {
          poll = null;
        }
        if (!poll || !poll.ok) {
          if (poll && poll.status === 404) {
            showPreviewMessage('error', 'error', '<strong>Job not found on the server.</strong><br>It may predate job persistence. Start a new production.');
            addLog('Follow stopped: job ' + jobId + ' is unknown on the server.', 'error');
            setJobState('error', 'Job not found.');
            break;
          }
          misses += 1;
          if (misses >= 120) {
            showPreviewMessage('error', 'error', '<strong>Lost contact with the console.</strong><br>The server may have restarted. Reload the page: the job restores from disk.');
            addLog('Lost contact with the console while following job ' + jobId + '.', 'error');
            setJobState('error', 'Lost contact with the console.');
            break;
          }
          currentStep.textContent = 'Reconnecting to the console (' + misses + 's).';
          showThinking('reconnecting');
          continue;
        }
        misses = 0;
        var job = await poll.json();
        syncProgressPills(job, seen);
        describeProgress(job);
        if (job.status === 'running' || job.status === 'queued') showProductionThinking(job);
        if (job && job.scenes && job.scenes.length) {
          if (sceneSig(job.scenes) !== boardSig && !editingScene()) renderStoryboard(job);
        }
        renderStoryCheck(job, null);
        if (job.status === 'done') {
          showFinishedJob(job, fallbackAspect);
          break;
        }
        if (job.status === 'error') {
          showFailedJob(job);
          break;
        }
        if (job.status === 'cancelled') {
          showCancelledJob(job);
          break;
        }
        if (job.status === 'interrupted') {
          showInterruptedJob(job);
          break;
        }
      }
    } finally {
      cancelBtn.hidden = true;
      cancelHint.hidden = true;
    }
  }

  function jobParam() {
    try {
      return new URLSearchParams(location.search).get('job');
    } catch (paramError) {
      return null;
    }
  }

  async function attachToJob(id, viaRefresh) {
    var resp = null;
    try {
      resp = await fetch('/api/job/' + id);
    } catch (fetchError) {
      return false;
    }
    if (!resp.ok) return false;
    var job = await resp.json();
    if (!job || !job.status) return false;
    rememberJob(id);
    var restoredBrief = job.brief || {};
    topicInput.value = job.topic || restoredBrief.topic || '';
    audienceInput.value = restoredBrief.audience || '';
    durationInput.value = restoredBrief.target_explicit ? (restoredBrief.target_duration_s || '') : '';
    if (job.style) setStylePick(job.style);
    if (job.color) setColorPick(job.color);
    customInput.value = job.style_custom || '';
    document.querySelectorAll('input[name="aspect"]').forEach(function (radio) {
      radio.checked = radio.value === (job.aspect_ratio || '9:16');
    });
    updateProjectTitle();
    syncTargetLength();
    if (job.status !== 'waiting_approval' && job.status !== 'approved') {
      var restoredLines = job.script || (job.scenes || []).map(function (scene) { return scene.narration; });
      var restoredVisuals = job.visuals || (job.scenes || []).map(function (scene) { return scene.visual_prompt || ''; });
      if (restoredLines.length) renderPlanEditor(restoredLines, restoredVisuals, 'live');
      if (job.scenes && job.scenes.length) renderStoryboard(job);
    }
    setPreviewAspect(job.aspect_ratio || selectedAspect());
    if (job.status === 'running' || job.status === 'queued') {
      resetProgress();
      addLog('Restored production ' + id + (viaRefresh ? ' after a page refresh.' : '.'), 'success');
      setJobState('producing', 'Production restored.');
      produceBtn.disabled = true;
      planBtn.disabled = true;
      produceBtnText.textContent = 'Creating…';
      setPlanLocked(true);
      try {
        await followJob(id, job.aspect_ratio);
      } finally {
        setPlanLocked(false);
        produceBtn.disabled = false;
        planBtn.disabled = false;
        produceBtnText.textContent = 'Approve & produce';
      }
      return true;
    }
    if (job.status === 'waiting_approval' || job.status === 'approved') {
      resetProgress();
      topicInput.value = job.topic || '';
      if (job.brief) {
        audienceInput.value = job.brief.audience || '';
        if (job.brief.target_duration_s && job.brief.target_explicit) {
          durationInput.value = job.brief.target_duration_s;
        }
      }
      renderApproval({
        job_id: job.id,
        script: job.script || [],
        visuals: job.visuals || (job.scenes || []).map(function (s) { return s.visual_prompt || ''; }),
        style: job.style || null,
        color: job.color || null,
        style_custom: job.style_custom || '',
        brief: job.brief || {},
        director: job.director || {},
        unmet_criteria: job.unmet_criteria || ((job.director || {}).unmet_criteria || []),
        editorial_pass: job.editorial_pass != null ? job.editorial_pass : ((job.director || {}).editorial_pass || false),
        revision_note: job.revision_note,
        duration_estimate: job.duration_estimate || {}
      });
      if (job.scenes && job.scenes.length) renderStoryboard(job);
      if (job.status === 'approved') {
        addLog('Plan ' + id + ' was already approved; its production runs separately.');
      } else {
        addLog('Restored waiting-approval plan ' + id + (viaRefresh ? ' after a page refresh.' : '.'), 'success');
      }
      return true;
    }
    resetProgress();
    syncProgressPills(job, null);
    if (job.scenes && job.scenes.length) renderStoryboard(job);
    renderStoryCheck(job, job.error || job.interrupt_reason || null);
    if (job.status === 'done') {
      showFinishedJob(job);
      addLog('Restored finished production ' + id + (viaRefresh ? ' after a page refresh.' : '.'), 'success');
      setJobState('done', 'Production restored.');
    } else if (job.status === 'error') {
      showFailedJob(job);
      addLog('Restored failed production ' + id + (viaRefresh ? ' after a page refresh.' : '.'), 'error');
    } else if (job.status === 'cancelled') {
      showCancelledJob(job);
      addLog('Restored cancelled production ' + id + (viaRefresh ? ' after a page refresh.' : '.'), 'error');
    } else if (job.status === 'interrupted') {
      showInterruptedJob(job);
      addLog('Restored interrupted production ' + id + (viaRefresh ? ' after a page refresh.' : '.'), 'error');
    }
    return true;
  }

  async function restoreLastJob() {
    var direct = jobParam();
    if (direct) {
      await attachToJob(direct, false);
      return;
    }
    var id = null;
    try {
      id = localStorage.getItem(LAST_JOB_KEY);
    } catch (storageError) {
      id = null;
    }
    if (!id) return;
    await attachToJob(id, true);
  }

  cancelBtn.addEventListener('click', async function () {
    if (!currentJobId) return;
    cancelBtn.disabled = true;
    try {
      var resp = await fetch('/api/job/' + currentJobId + '/cancel', { method: 'POST' });
      var data = await resp.json();
      if (!resp.ok) throw new Error((data && data.error) || 'cancel failed');
      addLog('Cancellation requested. ' + (data.note || 'The job stops at the next stage boundary.'));
    } catch (error) {
      addLog('Cancel failed: ' + error.message, 'error');
    } finally {
      cancelBtn.disabled = false;
    }
  });

  retryBtn.addEventListener('click', async function () {
    if (!currentJobId) return;
    retryBtn.disabled = true;
    try {
      var resp = await fetch('/api/job/' + currentJobId + '/retry', { method: 'POST' });
      var data = await resp.json();
      if (!resp.ok) throw new Error((data && data.error) || 'retry failed');
      clearProblem();
      resetProgress();
      previewMeta.hidden = true;
      outputVideo.removeAttribute('src');
      outputVideo.load();
      retryBtn.hidden = true;
      produceBtn.disabled = true;
      planBtn.disabled = true;
      produceBtnText.textContent = 'Creating…';
      setJobState('producing', 'Retrying production.');
      setPreviewAspect(selectedAspect());
      setPlanLocked(true);
      addLog('Retrying production ' + currentJobId + ' from the stored approved plan (full re-run, no re-revision).', 'success');
      await followJob(currentJobId, selectedAspect());
    } catch (error) {
      addLog('Retry failed: ' + error.message, 'error');
      showProblem('The retry could not start.', error.message);
    } finally {
      retryBtn.disabled = false;
      setPlanLocked(false);
      produceBtn.disabled = false;
      planBtn.disabled = false;
      produceBtnText.textContent = 'Approve & produce';
    }
  });

  async function startProduction(withDurationAck) {
    if (productionRunning) return;
    var topic = topicInput.value.trim();
    var plan = readEditedPlan();
    if (!plan.lines.length) {
      showProblem('The scene plan is empty.', 'Plan scenes first, keeping at least one line.');
      return;
    }
    if (plan.visuals.some(function (v) { return !v; })) {
      showProblem('A visual prompt is missing.', 'Every approved line needs its visual prompt before producing.');
      return;
    }
    var look = currentLook();
    var audience = audienceInput.value.trim();
    var targetRaw = durationInput.value;
    var target = null;
    if (targetRaw !== '' && targetRaw != null) {
      target = Number(targetRaw);
      if (!isFinite(target) || target <= 0) {
        showProblem('The target length must be a positive number of seconds.', 'Fix it in the brief, or leave it empty for auto.');
        durationInput.focus();
        return;
      }
    }
    var aspect = selectedAspect();
    setPreviewAspect(aspect);
    produceBtn.disabled = true;
    ackProduceBtn.hidden = true;
    planBtn.disabled = true;
    produceBtnText.textContent = 'Creating…';
    clearProblem();
    resetProgress();
    previewMeta.hidden = true;
    outputVideo.removeAttribute('src');
    outputVideo.load();
    setJobState('producing', 'Producing video.');
    showThinking('preparing');
    renderStoryCheck({ brief: { topic: topic, aspect_ratio: aspect } }, null);
    setPlanLocked(true);
    try {
      var body = { topic: topic, script: plan.lines, visuals: plan.visuals, aspect_ratio: aspect };
      if (look.style) body.style = look.style;
      if (look.color) body.color = look.color;
      if (look.style_custom) body.style_custom = look.style_custom;
      if (audience) body.audience = audience;
      if (target != null) body.target_duration_s = target;
      if (scriptJobId) body.script_job_id = scriptJobId;
      if (withDurationAck) body.duration_ack = true;
      var jobResponse = await fetch('/api/video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      var jobData = await jobResponse.json();
      if (!jobResponse.ok) {
        if (jobData && jobData.needs_duration_ack) {
          pendingDurationAck = true;
          durationWarn.hidden = false;
          durationWarn.textContent = jobData.error + '. ' + (jobData.suggestion || '');
          ackProduceBtnText.textContent = 'Approve ' + jobData.estimated_total_s + 's & produce';
          ackProduceBtn.hidden = false;
          showProblem('Duration approval needed: ' + jobData.error, jobData.suggestion + '. Press the amber button to approve this duration.');
          addLog('Duration gate: ' + jobData.error, 'error');
          showPreviewMessage('plan', 'rate_review', '<strong>Waiting for duration approval.</strong><br>' + escapeHtml(jobData.error) + '.');
          setJobState('waiting-approval', 'Waiting for duration approval.');
          currentStep.textContent = 'Waiting for approval: confirm the duration, then produce.';
          return;
        }
        var startMessage = String((jobData && jobData.error) || 'start failed');
        if (startMessage.indexOf('already running') !== -1) {
          showProblem('A production is already running.', 'Wait for it to finish, then try again.');
        } else if (startMessage.indexOf('No Meta API key') !== -1) {
          showProblem('No Meta API key found.', 'Paste your key in the brief.');
        } else if (startMessage.indexOf('approved') !== -1) {
          showProblem('No approved plan to produce.', 'Plan scenes first, review them, then approve.');
        } else {
          showProblem('The production could not start.', 'Open technical details, then try again.');
        }
        addLog('Start stopped: ' + startMessage, 'error');
        showPreviewMessage('error', 'error', '<strong>Could not start.</strong><br>' + escapeHtml(startMessage));
        setJobState('error', 'Production could not start.');
        return;
      }
      var jobId = jobData.job_id;
      pendingDurationAck = false;
      addLog('Approval recorded. Production running (' + aspect + '): the approved lines are used exactly, nothing is re-written.', 'success');
      setPlanLocked(true);
      await followJob(jobId, aspect);
    } catch (error) {
      var message = String((error && error.message) || 'production failed');
      markProgressError();
      showPreviewMessage('error', 'error', '<strong>Production failed.</strong><br>' + escapeHtml(message));
      showProblem('The production failed before finishing.', 'Open technical details, then try again.');
      addLog('Production stopped: ' + message, 'error');
      setJobState('error', 'Production failed.');
      currentStep.textContent = 'Production failed.';
    } finally {
      setPlanLocked(false);
      produceBtn.disabled = false;
      planBtn.disabled = false;
      produceBtnText.textContent = 'Approve & produce';
    }
  }

  produceBtn.addEventListener('click', function () { startProduction(false); });
  ackProduceBtn.addEventListener('click', function () { startProduction(true); });

  syncTargetLength();
  checkHealth();
  setPreviewAspect(selectedAspect());
  loadStyleOptions().then(restoreLastJob);
