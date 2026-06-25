// Haruka Kajita VPM listing — UI の多言語化（UnityEditorLocalization と同じ言語種）と
// Add to VCC / URL コピーの挙動。Fluent UI には依存しない素の JS。
// パッケージ一覧は index.html 側で Scriban が描画するため、ここに {{ }} テンプレート変数は持たない。

const LANGS = [
	{ code: "ja", label: "日本語" },
	{ code: "en", label: "English" },
	{ code: "zh-Hans", label: "简体中文" },
	{ code: "zh-Hant", label: "繁體中文" },
	{ code: "ko", label: "한국어" },
	{ code: "fr", label: "Français" },
	{ code: "de", label: "Deutsch" },
	{ code: "it", label: "Italiano" },
	{ code: "es-ES", label: "Español (España)" },
	{ code: "es-419", label: "Español (Latinoamérica)" },
	{ code: "pt-BR", label: "Português (Brasil)" },
	{ code: "pt-PT", label: "Português (Portugal)" },
	{ code: "ru", label: "Русский" },
	{ code: "pl", label: "Polski" },
	{ code: "tr", label: "Türkçe" },
	{ code: "th", label: "ไทย" },
	{ code: "vi", label: "Tiếng Việt" },
	{ code: "uk", label: "Українська" },
	{ code: "id", label: "Bahasa Indonesia" },
];

const STRINGS = {
	"ja": { tagline: "Unity エディタ拡張パッケージの配布リポジトリ", copy: "コピー", copied: "コピーしました", addNote: "ボタンで VCC / ALCOM が開かない場合は、URL をコピーしてリポジトリを手動で追加してください。", howTo: "追加方法（VCC / ALCOM）", howToBody: "VCC または ALCOM の Settings → Packages → Add Repository を開き、上の URL を貼り付けて確定します。追加後、各プロジェクトでパッケージを選べます。", packagesTitle: "収録パッケージ", by: "提供:", authorSite: "作者のサイト" },
	"en": { tagline: "A package listing for Unity editor extensions", copy: "Copy", copied: "Copied", addNote: "If the button doesn't open VCC / ALCOM, copy the URL and add the repository manually.", howTo: "How to add (VCC / ALCOM)", howToBody: "In VCC or ALCOM, open Settings → Packages → Add Repository, paste the URL above, and confirm. Then pick the packages in each project.", packagesTitle: "Packages", by: "by", authorSite: "Author's site" },
	"zh-Hans": { tagline: "Unity 编辑器扩展包的分发仓库", copy: "复制", copied: "已复制", addNote: "如果按钮无法打开 VCC / ALCOM，请复制 URL 并手动添加仓库。", howTo: "添加方法（VCC / ALCOM）", howToBody: "在 VCC 或 ALCOM 中打开 Settings → Packages → Add Repository，粘贴上面的 URL 并确认。添加后即可在各项目中选择软件包。", packagesTitle: "包含的软件包", by: "作者：", authorSite: "作者网站" },
	"zh-Hant": { tagline: "Unity 編輯器擴充套件的發佈儲存庫", copy: "複製", copied: "已複製", addNote: "若按鈕無法開啟 VCC / ALCOM，請複製 URL 並手動新增儲存庫。", howTo: "新增方式（VCC / ALCOM）", howToBody: "在 VCC 或 ALCOM 開啟 Settings → Packages → Add Repository，貼上上方的 URL 並確認。新增後即可在各專案中選擇套件。", packagesTitle: "收錄套件", by: "作者：", authorSite: "作者網站" },
	"ko": { tagline: "Unity 에디터 확장 패키지 배포 리포지토리", copy: "복사", copied: "복사됨", addNote: "버튼으로 VCC / ALCOM이 열리지 않으면 URL을 복사해 저장소를 수동으로 추가하세요.", howTo: "추가 방법 (VCC / ALCOM)", howToBody: "VCC 또는 ALCOM에서 Settings → Packages → Add Repository를 열고 위의 URL을 붙여넣어 확인하세요. 추가 후 각 프로젝트에서 패키지를 선택할 수 있습니다.", packagesTitle: "포함된 패키지", by: "제공:", authorSite: "제작자 사이트" },
	"fr": { tagline: "Un dépôt de paquets pour extensions de l'éditeur Unity", copy: "Copier", copied: "Copié", addNote: "Si le bouton n'ouvre pas VCC / ALCOM, copiez l'URL et ajoutez le dépôt manuellement.", howTo: "Comment ajouter (VCC / ALCOM)", howToBody: "Dans VCC ou ALCOM, ouvrez Settings → Packages → Add Repository, collez l'URL ci-dessus et confirmez. Sélectionnez ensuite les paquets dans chaque projet.", packagesTitle: "Paquets", by: "par", authorSite: "Site de l'auteur" },
	"de": { tagline: "Ein Paket-Verzeichnis für Unity-Editor-Erweiterungen", copy: "Kopieren", copied: "Kopiert", addNote: "Falls die Schaltfläche VCC / ALCOM nicht öffnet, kopiere die URL und füge das Repository manuell hinzu.", howTo: "Hinzufügen (VCC / ALCOM)", howToBody: "Öffne in VCC oder ALCOM Settings → Packages → Add Repository, füge die obige URL ein und bestätige. Wähle danach die Pakete in jedem Projekt.", packagesTitle: "Pakete", by: "von", authorSite: "Website des Autors" },
	"it": { tagline: "Un elenco di pacchetti per estensioni dell'editor di Unity", copy: "Copia", copied: "Copiato", addNote: "Se il pulsante non apre VCC / ALCOM, copia l'URL e aggiungi il repository manualmente.", howTo: "Come aggiungere (VCC / ALCOM)", howToBody: "In VCC o ALCOM apri Settings → Packages → Add Repository, incolla l'URL qui sopra e conferma. Poi seleziona i pacchetti in ogni progetto.", packagesTitle: "Pacchetti", by: "di", authorSite: "Sito dell'autore" },
	"es-ES": { tagline: "Un repositorio de paquetes para extensiones del editor de Unity", copy: "Copiar", copied: "Copiado", addNote: "Si el botón no abre VCC / ALCOM, copia la URL y añade el repositorio manualmente.", howTo: "Cómo añadir (VCC / ALCOM)", howToBody: "En VCC o ALCOM, abre Settings → Packages → Add Repository, pega la URL de arriba y confirma. Después selecciona los paquetes en cada proyecto.", packagesTitle: "Paquetes", by: "por", authorSite: "Sitio del autor" },
	"es-419": { tagline: "Un repositorio de paquetes para extensiones del editor de Unity", copy: "Copiar", copied: "Copiado", addNote: "Si el botón no abre VCC / ALCOM, copia la URL y agrega el repositorio manualmente.", howTo: "Cómo agregar (VCC / ALCOM)", howToBody: "En VCC o ALCOM, abre Settings → Packages → Add Repository, pega la URL de arriba y confirma. Luego selecciona los paquetes en cada proyecto.", packagesTitle: "Paquetes", by: "por", authorSite: "Sitio del autor" },
	"pt-BR": { tagline: "Um repositório de pacotes para extensões do editor do Unity", copy: "Copiar", copied: "Copiado", addNote: "Se o botão não abrir o VCC / ALCOM, copie a URL e adicione o repositório manualmente.", howTo: "Como adicionar (VCC / ALCOM)", howToBody: "No VCC ou ALCOM, abra Settings → Packages → Add Repository, cole a URL acima e confirme. Depois selecione os pacotes em cada projeto.", packagesTitle: "Pacotes", by: "por", authorSite: "Site do autor" },
	"pt-PT": { tagline: "Um repositório de pacotes para extensões do editor do Unity", copy: "Copiar", copied: "Copiado", addNote: "Se o botão não abrir o VCC / ALCOM, copie o URL e adicione o repositório manualmente.", howTo: "Como adicionar (VCC / ALCOM)", howToBody: "No VCC ou ALCOM, abra Settings → Packages → Add Repository, cole o URL acima e confirme. Depois selecione os pacotes em cada projeto.", packagesTitle: "Pacotes", by: "por", authorSite: "Site do autor" },
	"ru": { tagline: "Репозиторий пакетов для расширений редактора Unity", copy: "Копировать", copied: "Скопировано", addNote: "Если кнопка не открывает VCC / ALCOM, скопируйте URL и добавьте репозиторий вручную.", howTo: "Как добавить (VCC / ALCOM)", howToBody: "В VCC или ALCOM откройте Settings → Packages → Add Repository, вставьте URL выше и подтвердите. Затем выбирайте пакеты в каждом проекте.", packagesTitle: "Пакеты", by: "автор:", authorSite: "Сайт автора" },
	"pl": { tagline: "Repozytorium pakietów dla rozszerzeń edytora Unity", copy: "Kopiuj", copied: "Skopiowano", addNote: "Jeśli przycisk nie otwiera VCC / ALCOM, skopiuj URL i dodaj repozytorium ręcznie.", howTo: "Jak dodać (VCC / ALCOM)", howToBody: "W VCC lub ALCOM otwórz Settings → Packages → Add Repository, wklej powyższy URL i zatwierdź. Następnie wybierz pakiety w każdym projekcie.", packagesTitle: "Pakiety", by: "autor:", authorSite: "Strona autora" },
	"tr": { tagline: "Unity editör uzantıları için paket deposu", copy: "Kopyala", copied: "Kopyalandı", addNote: "Düğme VCC / ALCOM'u açmazsa URL'yi kopyalayıp depoyu elle ekleyin.", howTo: "Nasıl eklenir (VCC / ALCOM)", howToBody: "VCC veya ALCOM'da Settings → Packages → Add Repository'yi açın, yukarıdaki URL'yi yapıştırın ve onaylayın. Ardından her projede paketleri seçin.", packagesTitle: "Paketler", by: "yazan:", authorSite: "Yazarın sitesi" },
	"th": { tagline: "ที่เก็บแพ็กเกจสำหรับส่วนขยายของ Unity Editor", copy: "คัดลอก", copied: "คัดลอกแล้ว", addNote: "หากปุ่มไม่เปิด VCC / ALCOM ให้คัดลอก URL แล้วเพิ่มที่เก็บด้วยตนเอง", howTo: "วิธีเพิ่ม (VCC / ALCOM)", howToBody: "ใน VCC หรือ ALCOM ให้เปิด Settings → Packages → Add Repository วาง URL ด้านบนแล้วยืนยัน จากนั้นเลือกแพ็กเกจในแต่ละโปรเจกต์", packagesTitle: "แพ็กเกจ", by: "โดย", authorSite: "เว็บไซต์ผู้สร้าง" },
	"vi": { tagline: "Kho gói cho các tiện ích mở rộng của Unity Editor", copy: "Sao chép", copied: "Đã sao chép", addNote: "Nếu nút không mở VCC / ALCOM, hãy sao chép URL và thêm kho thủ công.", howTo: "Cách thêm (VCC / ALCOM)", howToBody: "Trong VCC hoặc ALCOM, mở Settings → Packages → Add Repository, dán URL ở trên và xác nhận. Sau đó chọn các gói trong từng dự án.", packagesTitle: "Các gói", by: "bởi", authorSite: "Trang của tác giả" },
	"uk": { tagline: "Репозиторій пакетів для розширень редактора Unity", copy: "Копіювати", copied: "Скопійовано", addNote: "Якщо кнопка не відкриває VCC / ALCOM, скопіюйте URL і додайте репозиторій вручну.", howTo: "Як додати (VCC / ALCOM)", howToBody: "У VCC або ALCOM відкрийте Settings → Packages → Add Repository, вставте URL вище та підтвердьте. Потім вибирайте пакети в кожному проєкті.", packagesTitle: "Пакети", by: "автор:", authorSite: "Сайт автора" },
	"id": { tagline: "Repositori paket untuk ekstensi editor Unity", copy: "Salin", copied: "Tersalin", addNote: "Jika tombol tidak membuka VCC / ALCOM, salin URL dan tambahkan repositori secara manual.", howTo: "Cara menambahkan (VCC / ALCOM)", howToBody: "Di VCC atau ALCOM, buka Settings → Packages → Add Repository, tempel URL di atas, lalu konfirmasi. Setelah itu pilih paket di tiap proyek.", packagesTitle: "Paket", by: "oleh", authorSite: "Situs penulis" },
};

const SUPPORTED = LANGS.map((l) => l.code);
const STORAGE_KEY = "vpm-listing-lang";

function pickInitialLang() {
	const saved = localStorage.getItem(STORAGE_KEY);
	if (saved && STRINGS[saved]) return saved;
	const candidates = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ""];
	for (const raw of candidates) {
		const lower = String(raw).toLowerCase();
		const exact = SUPPORTED.find((c) => c.toLowerCase() === lower);
		if (exact) return exact;
		const base = lower.split("-")[0];
		const baseMatch = SUPPORTED.find((c) => c.toLowerCase() === base || c.toLowerCase().startsWith(base + "-"));
		if (baseMatch) return baseMatch;
	}
	return "ja";
}

function applyLang(lang) {
	const dict = STRINGS[lang] || STRINGS.ja;
	document.documentElement.lang = lang;
	document.querySelectorAll("[data-i18n]").forEach((el) => {
		const key = el.getAttribute("data-i18n");
		if (dict[key] != null) el.textContent = dict[key];
	});
	const select = document.getElementById("langSelect");
	if (select) select.value = lang;
	try {
		localStorage.setItem(STORAGE_KEY, lang);
	} catch (_) {
		/* localStorage 不可の環境では永続化しない */
	}
}

function currentDict() {
	return STRINGS[document.documentElement.lang] || STRINGS.ja;
}

async function copyText(text) {
	try {
		await navigator.clipboard.writeText(text);
		return;
	} catch (_) {
		const ta = document.createElement("textarea");
		ta.value = text;
		ta.setAttribute("readonly", "");
		ta.style.position = "absolute";
		ta.style.left = "-9999px";
		document.body.appendChild(ta);
		ta.select();
		try {
			document.execCommand("copy");
		} catch (_) {
			/* コピー不可の環境では URL を手動選択してもらう */
		}
		document.body.removeChild(ta);
	}
}

function init() {
	const select = document.getElementById("langSelect");
	if (select) {
		LANGS.forEach((l) => {
			const opt = document.createElement("option");
			opt.value = l.code;
			opt.textContent = l.label;
			select.appendChild(opt);
		});
		select.addEventListener("change", () => applyLang(select.value));
	}
	applyLang(pickInitialLang());

	const urlEl = document.getElementById("listingUrl");
	const url = urlEl ? urlEl.textContent.trim() : "";

	// Add to VCC: スキームに合わせて URL をエンコードした href へ差し替える（生 href は no-JS 用フォールバック）。
	const addBtn = document.getElementById("addBtn");
	if (addBtn && url) {
		addBtn.href = "vcc://vpm/addRepo?url=" + encodeURIComponent(url);
	}

	// URL コピー（成功時はラベルを一時的に「コピーしました」へ）。
	const copyBtn = document.getElementById("copyBtn");
	if (copyBtn && url) {
		const label = copyBtn.querySelector("[data-i18n]");
		copyBtn.addEventListener("click", async () => {
			await copyText(url);
			if (label) label.textContent = currentDict().copied;
			copyBtn.classList.add("is-copied");
			window.setTimeout(() => {
				if (label) label.textContent = currentDict().copy;
				copyBtn.classList.remove("is-copied");
			}, 1500);
		});
	}
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", init);
} else {
	init();
}
