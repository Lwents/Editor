const $=id=>document.getElementById(id);
window.editorSettings.get().then(c=>{$('url').value=c.url;$('model').value=c.model;$('cpu').checked=c.cpu;if(c.hasSoundKey)$('soundKey').placeholder='Đã lưu khóa; để trống để giữ nguyên';if(c.hasKey)$('key').placeholder='Đã lưu khóa; để trống để giữ nguyên';});
$('save').onclick=async()=>{try{await window.editorSettings.save({url:$('url').value.trim(),model:$('model').value.trim(),key:$('key').value,soundKey:$('soundKey').value,cpu:$('cpu').checked});}catch(e){$('error').textContent=e.message;}};
