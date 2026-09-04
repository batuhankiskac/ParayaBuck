# C2 R4 provenance denetimi

R4 manifest self-hash’i `d89c0ddcbac421682c6f6b59cc6e989cd3e2eb1f6e1d614834bf57581b4e369d` olarak doğrulandı. Hesap, `manifest_sha256` alanı çıkarılmış canonical JSON’in `sort_keys=True` ve `separators=(',', ':')` ile SHA-256’lanmasına dayanır. Raw frozen-file hash’i `f44d2042bcf3a5a1a4c65f8caa02a569606ce67cb3cbc6b686f2395d238cbf7f` ayrı bir dosya bütünlüğü kimliğidir; manifest self-hash’iyle aynı şey değildir.

Etkin manifest source pinleri gerçek mevcut kaynak dosyalarıyla eşleşir: candidate `13906b6c...e292c4`, engine `7be4daf4...debd`. Runner, `source.file_sha256` alanını gerçek dosya SHA’sıyla karşılaştırır ve uyuşmazlıkta kapanır. `require_frozen_inputs` ayrıca seçili manifest/receipt input SHA’larını ve source HEAD’ini doğrular.

Receipt içindeki nested `source.file_sha256` değerleri eski generator provenance’ıdır (`candidate=e09a1b34...`, `engine=49d71090...`) ve etkin guard tarafından okunmaz. Receipt aynen korunmuştur; bu stale metadata ekonomik kanıt veya yeni bir source pin olarak yorumlanmamalıdır. R4’ün seçili receipt manifest hash’i etkin manifest hash’iyle aynıdır.

Bu kayıt yalnız provenance sınıflandırmasıdır; ekonomik sonuç, aday kararı ve frozen input dosyaları üzerinde değişiklik yapmaz.
