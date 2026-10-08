(() => {
  const form = document.getElementById('email-connection-form');
  if (!form) return;
  const provider = document.getElementById('id_provider');
  const auth = form.elements.auth_type;
  const source = form.elements.credential_source;
  const update = () => {
    const selected = provider.value;
    form.querySelectorAll('[data-email-providers], [data-auth-types], [data-credential-source]').forEach((element) => {
      const providers = element.dataset.emailProviders;
      const authTypes = element.dataset.authTypes;
      const sourceType = element.dataset.credentialSource;
      const hidden = Boolean(
        (providers && !providers.split(' ').includes(selected)) ||
        (authTypes && selected === 'custom_https' && !authTypes.split(' ').includes(auth.value)) ||
        (authTypes && !providers && selected !== 'custom_https') ||
        (sourceType && selected === 'amazon_ses' && source.value !== sourceType)
      );
      element.hidden = hidden;
      element.style.display = hidden ? 'none' : '';
    });
  };
  provider.addEventListener('change', () => {
    const defaults = { sendgrid: 'global', mailgun: 'us', amazon_ses: 'eu-west-1' };
    form.elements.region.value = defaults[provider.value] || '';
    update();
  });
  auth.addEventListener('change', update);
  source.addEventListener('change', update);
  update();

  const picker = document.getElementById('email-mapping-field');
  const payload = form.elements.payload;
  picker.addEventListener('change', () => {
    const field = picker.value;
    if (!field) return;
    const binding = ['message.to', 'message.cc', 'message.bcc', 'message.reply_to'].includes(field)
      ? { $each: field, $template: { $value: 'item.address' } }
      : field === 'message.attachments'
        ? { $each: field, $template: { filename: { $value: 'item.filename' },
            content: { $value: 'item.content_base64' }, type: { $value: 'item.content_type' } } }
        : { $value: field };
    const text = JSON.stringify(binding);
    const start = payload.selectionStart;
    payload.setRangeText(text, start, payload.selectionEnd, 'end');
    payload.focus();
    picker.value = '';
  });
  form.querySelectorAll('[data-preview-kind]').forEach((button) => {
    button.addEventListener('click', () => { form.elements.kind.value = button.dataset.previewKind; });
  });
})();
