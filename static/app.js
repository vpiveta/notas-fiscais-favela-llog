const cpf = document.querySelector('#cpf');
if (cpf) cpf.addEventListener('input', () => {
  let v = cpf.value.replace(/\D/g, '').slice(0, 11);
  v = v.replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d{1,2})$/, '$1-$2');
  cpf.value = v;
});
const pdf = document.querySelector('#pdf');
if (pdf) pdf.addEventListener('change', () => {
  const file = pdf.files[0];
  document.querySelector('#file-label').textContent = file ? file.name : 'Selecionar nota fiscal em PDF';
  document.querySelector('#dropzone').classList.toggle('selected', Boolean(file));
});
const form = document.querySelector('#upload-form');
if (form) form.addEventListener('submit', () => {
  const btn = document.querySelector('#submit-btn'); btn.disabled = true; btn.textContent = 'Enviando...';
});

