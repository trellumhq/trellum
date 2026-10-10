/* Saved-connection probes are separate from the shared settings save handler. */
(function () {
  var form = document.getElementById('orgAssistantForm');
  if (!form || form.dataset.assistantInitialized) return;
  form.dataset.assistantInitialized = 'true';
  var presets = JSON.parse(document.getElementById('assistant-provider-presets').textContent),
      provider = form.elements.provider, auth = form.elements.auth_mode, model = form.elements.model,
      testButton = document.getElementById('assistant-test'), modelsButton = document.getElementById('assistant-models'),
      modelStatus = document.getElementById('assistant-model-status'),
      out = document.getElementById('assistant-test-result'),
      revision = Number(form.dataset.configRevision), saved = !testButton.disabled,
      generation = 0, pending = null,
      previousProvider = provider.value, endpointDrafts = {}, modelDrafts = {},
      savedConnection = JSON.parse(document.getElementById('assistant-saved-connection').textContent),
      credentialNames = ['api_key', 'access_key_id', 'secret_access_key', 'session_token', 'client_secret', 'service_account'],
      identityNames = ['provider', 'auth_mode', 'base_url', 'region', 'tenant_id', 'client_id', 'project', 'location'].concat(credentialNames);
  model.setAttribute('list', 'assistant-model-list');

  function fields() {
    var preset = presets[provider.value], modes = preset.auth_modes;
    Array.from(auth.options).forEach(function (option) {
      option.hidden = option.disabled = modes.indexOf(option.value) === -1;
    });
    if (modes.indexOf(auth.value) === -1) auth.value = modes[0];
    form.querySelectorAll('[data-provider-fields], [data-auth-fields]').forEach(function (group) {
      var visible = (!group.dataset.providerFields || group.dataset.providerFields.split(' ').indexOf(provider.value) !== -1)
          && (!group.dataset.authFields || group.dataset.authFields.split(' ').indexOf(auth.value) !== -1);
      if (!visible && group.contains(document.activeElement)) provider.focus({preventScroll: true});
      group.hidden = !visible;
    });
    form.querySelector('[data-endpoint-fields]').hidden = provider.value === 'bedrock' || provider.value === 'vertex';
    if (['azure', 'litellm', 'custom'].indexOf(provider.value) !== -1 || auth.value === 'none') form.querySelector('details').open = true;
    form.elements.base_url.placeholder = preset.base_url || (provider.value === 'azure' ? 'https://your-resource.openai.azure.com' : 'https://gateway.example/v1');
    model.placeholder = preset.default_model || (provider.value === 'azure' ? 'Deployment name' : 'Model ID');
    var hint = document.getElementById('gateway-hint');
    hint.textContent = provider.value === 'azure' ? 'Use your HTTPS Azure resource endpoint and enter the deployment name as the model.'
      : preset.protocol === 'openai' ? 'The endpoint must support OpenAI Chat Completions, streaming, and tools. Gateways own their routing and fallbacks.'
      : preset.protocol === 'anthropic' ? 'The endpoint must implement Anthropic Messages, streaming, and tools.' : '';
    var vendor = /^claude-/i.test(model.value) ? 'anthropic' : /^(gpt-|o1|o3|o4)/i.test(model.value) ? 'openai' : '',
        modelHint = document.getElementById('model-hint');
    modelHint.textContent = vendor && vendor !== provider.value ? modelHint.getAttribute('data-' + provider.value) || '' : '';
  }
  function sameConnection() {
    if (!savedConnection) return false;
    var cloud = {}, names = provider.value === 'bedrock' ? ['region'] : provider.value === 'vertex' ? ['project', 'location']
        : provider.value === 'azure' && auth.value === 'client_secret' ? ['tenant_id', 'client_id'] : [];
    names.forEach(function (name) { cloud[name] = form.elements[name].value.trim(); });
    var endpoint = (form.elements.base_url.value.trim() || presets[provider.value].base_url).replace(/\/+$/, '');
    return savedConnection.provider === provider.value && savedConnection.auth_mode === auth.value && savedConnection.base_url === endpoint
      && names.every(function (name) { return cloud[name] === savedConnection.cloud_config[name]; })
      && !credentialNames.some(function (name) { return form.elements[name].value !== ''; });
  }
  function sync() {
    var disabled = !saved || !!pending || form.getAttribute('aria-busy') === 'true' || !sameConnection();
    modelsButton.disabled = disabled;
    testButton.disabled = disabled || !savedConnection || (model.value.trim() || presets[provider.value].default_model) !== (savedConnection.model || presets[provider.value].default_model);
  }
  function invalidate(clearModels) {
    ++generation;
    if (pending) pending.abort();
    pending = null;
    out.hidden = true;
    modelStatus.textContent = 'Discovery uses saved credentials. You can always enter a model ID or Azure deployment manually.';
    if (clearModels === true) document.getElementById('assistant-model-list').replaceChildren();
    sync();
  }
  function checks(values) {
    ['chat', 'alerts', 'usage'].forEach(function (name) {
      var target = document.querySelector('[data-settings-text="assistant-' + name + '-check"]'), check = values[name],
          label = name === 'usage' ? 'Cost metering' : name === 'alerts' ? 'Alerts' : 'Chat';
      target.textContent = label + ': ' + (check ? (check.ok ? 'Verified' : 'Unavailable') + ' — ' + (check.message || '') : 'Not tested');
    });
  }
  function show(result) {
    out.hidden = false;
    out.classList.toggle('success', result.ok === true);
    out.classList.toggle('error', result.ok === false);
    out.querySelector('.ui-flash-mark').textContent = result.ok === true ? '✓' : result.ok === false ? '×' : '';
    document.getElementById('assistant-test-message').textContent = result.message || '';
    document.getElementById('assistant-test-latency').textContent = result.latency_ms == null ? '' : result.latency_ms + ' ms';
    var hints = (result.hints || []).slice();
    if (result.assistant_available === false && result.assistant_reason) hints.push(result.assistant_reason);
    document.getElementById('assistant-test-hints').textContent = hints.join(' ');
    var details = document.getElementById('assistant-test-details');
    details.hidden = !result.detail; details.open = false;
    details.querySelector('pre').textContent = result.detail || '';
  }
  async function probe(kind) {
    if ((kind === 'test' ? testButton : modelsButton).disabled) return;
    var token = ++generation, testedRevision = revision, controller = new AbortController(), timedOut = false;
    pending = controller;
    sync();
    if (kind === 'test') show({message: 'Testing streaming, tools, alert compatibility and cost metering through saved settings…'});
    else modelStatus.textContent = 'Discovering models through saved credentials…';
    var timer = setTimeout(function () { timedOut = true; controller.abort(); }, 60000);
    try {
      var response = await fetch(form.dataset[kind === 'test' ? 'testUrl' : 'modelsUrl'], {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'X-CSRFToken': form.elements.csrfmiddlewaretoken.value}
      });
      if (!response.ok) throw new Error('Request failed');
      var result = await response.json();
      if (token !== generation || testedRevision !== revision) return;
      if (result.stale || Number(result.config_revision) !== revision) {
        if (kind === 'test') show({ok: false, message: 'Settings changed while testing. Test the current saved connection again.'});
        else modelStatus.textContent = 'Settings changed during discovery. Discover the current saved connection again.';
        return;
      }
      if (kind === 'test') {
        show(result); checks(result.checks || {});
        readiness(result.assistant_available, result.assistant_reason);
      } else {
        var list = document.getElementById('assistant-model-list');
        list.replaceChildren();
        if (provider.value !== 'azure') (result.models || []).forEach(function (item) {
          var option = document.createElement('option'); option.value = item.id; option.label = item.label || item.id; list.appendChild(option);
        });
        modelStatus.textContent = result.message || 'Models loaded; you can also enter a model ID manually.';
      }
    } catch (error) {
      if (token !== generation) return;
      var message = timedOut ? 'The request timed out. Try again.' : 'The request did not complete.';
      if (kind === 'test') show({ok: false, message: message});
      else modelStatus.textContent = message + ' Enter the model ID or deployment name manually.';
    } finally {
      clearTimeout(timer);
      if (token === generation) { pending = null; sync(); }
    }
  }
  function readiness(ready, reason) {
    if (typeof ready !== 'boolean') return;
    var status = document.getElementById('assistant-readiness');
    status.classList.toggle('success', ready); status.classList.toggle('error', !ready);
    document.getElementById('assistant-readiness-mark').textContent = ready ? '✓' : '×';
    document.querySelector('[data-settings-text="assistant-readiness-title"]').textContent = ready ? 'AI is ready based on saved settings.' : 'AI is unavailable based on saved settings.';
    document.querySelector('[data-settings-text="assistant-readiness-reason"]').textContent = reason || 'Use Test connection below to verify provider access.';
  }
  function edited(event) {
    if (event.target.name === 'provider' && previousProvider !== provider.value) {
      endpointDrafts[previousProvider] = form.elements.base_url.value;
      modelDrafts[previousProvider] = model.value;
      form.elements.base_url.value = endpointDrafts[provider.value] || '';
      model.value = modelDrafts[provider.value] || '';
      credentialNames.forEach(function (name) { form.elements[name].value = ''; });
      previousProvider = provider.value;
    }
    fields(); invalidate(identityNames.indexOf(event.target.name) !== -1);
  }
  form.addEventListener('input', edited);
  form.addEventListener('change', edited);
  form.addEventListener('submit', function () { invalidate(); });
  form.addEventListener('settings:success', function (event) {
    var previous = savedConnection;
    savedConnection = event.detail.saved_connection;
    invalidate(JSON.stringify(previous) !== JSON.stringify(savedConnection)); saved = true;
    revision = Number(event.detail.config_revision);
    form.dataset.configRevision = revision;
    form.elements.config_revision.value = revision;
    if (event.detail.assistant_open_pricing === true) form.querySelector('details').open = true;
    readiness(event.detail.assistant_ready, (event.detail.updates || {})['assistant-readiness-reason']);
    document.getElementById('assistant-cloud-stored').textContent = event.detail.has_cloud_credentials ? 'Cloud credentials are stored; blank fields keep them only for the same connection identity.' : '';
    sync();
  });
  new MutationObserver(function () {
    if (form.getAttribute('aria-busy') === 'true') invalidate(); else sync();
  }).observe(form, {attributes: true, attributeFilter: ['aria-busy']});
  window.addEventListener('pagehide', invalidate);
  testButton.addEventListener('click', function () { probe('test'); });
  modelsButton.addEventListener('click', function () { probe('models'); });
  fields(); sync();
})();
