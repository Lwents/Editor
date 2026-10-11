use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SubtitleExportOptions {
    pub format: String,
    pub name: String,
    pub width: u32,
    pub height: u32,
    pub cues: Vec<SubtitleExportCue>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SubtitleExportCue {
    pub start_time: f64,
    pub duration: f64,
    pub params: SubtitleExportParams,
}
#[derive(Deserialize, Default)]
#[serde(default, rename_all = "camelCase")]
pub struct SubtitleExportParams {
    pub content: String,
    pub font_family: Option<String>,
    pub font_size: Option<f64>,
    pub font_weight: Option<String>,
    pub font_style: Option<String>,
    pub color: Option<String>,
    #[serde(rename = "transform.positionX")]
    pub x: f64,
    #[serde(rename = "transform.positionY")]
    pub y: f64,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SubtitleExportFile {
    pub filename: String,
    pub content: String,
}
fn timestamp(seconds: f64, ass: bool) -> String {
    let units = if ass { 100.0 } else { 1000.0 };
    let total = (seconds.max(0.0) * units).round() as u64;
    let fraction = total % units as u64;
    let whole = total / units as u64;
    let (h, m, s) = (whole / 3600, whole / 60 % 60, whole % 60);
    if ass {
        format!("{h}:{m:02}:{s:02}.{fraction:02}")
    } else {
        format!("{h:02}:{m:02}:{s:02},{fraction:03}")
    }
}
fn ass_color(color: &str) -> String {
    let c = color.trim_start_matches('#');
    if c.len() >= 6 && c.is_ascii() && c[..6].bytes().all(|b| b.is_ascii_hexdigit()) {
        format!("&H{}{}{}&", &c[4..6], &c[2..4], &c[0..2])
    } else {
        "&HFFFFFF&".into()
    }
}
pub fn export_subtitles(mut options: SubtitleExportOptions) -> Result<SubtitleExportFile, String> {
    let ass = match options.format.as_str() {
        "srt" => false,
        "ass" => true,
        _ => return Err("Định dạng phụ đề không hợp lệ.".into()),
    };
    options.cues.retain(|c| {
        c.start_time.is_finite()
            && c.duration.is_finite()
            && c.duration > 0.0
            && c.start_time + c.duration > 0.0
            && !c.params.content.trim().is_empty()
    });
    options
        .cues
        .sort_by(|a, b| a.start_time.total_cmp(&b.start_time));
    if options.cues.is_empty() {
        return Err("Chưa có phụ đề để xuất.".into());
    }
    let mut content = if ass {
        format!(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: {}\nPlayResY: {}\nWrapStyle: 2\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
            options.width.max(1),
            options.height.max(1)
        )
    } else {
        String::new()
    };
    for (index, cue) in options.cues.iter().enumerate() {
        let start = timestamp(cue.start_time, ass);
        // Keep short cues valid after rounding to the output format's precision.
        let end = timestamp(
            (cue.start_time + cue.duration)
                .max(cue.start_time.max(0.0) + if ass { 0.01 } else { 0.001 }),
            ass,
        );
        let text = cue
            .params
            .content
            .trim()
            .replace("\r\n", "\n")
            .replace('\r', "\n");
        if ass {
            let p = &cue.params;
            let font = p
                .font_family
                .as_deref()
                .unwrap_or("Arial")
                .replace(['{', '}', '\\', '\n', '\r'], "");
            let pixels = p.font_size.unwrap_or(5.0) * options.height.max(1) as f64 / 90.0;
            let color = ass_color(p.color.as_deref().unwrap_or("#ffffff"));
            let bold = i32::from(p.font_weight.as_deref() == Some("bold"));
            let italic = i32::from(p.font_style.as_deref() == Some("italic"));
            let x = options.width as f64 / 2.0 + p.x;
            let y = options.height as f64 / 2.0 + p.y;
            let text = text
                .replace('\\', "\\\\")
                .replace('{', "\\{")
                .replace('}', "\\}")
                .replace('\n', "\\N");
            content.push_str(&format!("Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\an5\\pos({x:.2},{y:.2})\\fn{font}\\fs{pixels:.2}\\c{color}\\b{bold}\\i{italic}}}{text}\n"));
        } else {
            content.push_str(&format!("{}\n{start} --> {end}\n{text}\n\n", index + 1));
        }
    }
    let name: String = options
        .name
        .chars()
        .map(|c| {
            if c.is_control() || "<>:\"/\\|?*".contains(c) {
                '_'
            } else {
                c
            }
        })
        .take(120)
        .collect();
    let name = name.trim_matches([' ', '.']);
    Ok(SubtitleExportFile {
        filename: format!(
            "{}.{}",
            if name.is_empty() { "phu-de" } else { name },
            options.format
        ),
        content,
    })
}
