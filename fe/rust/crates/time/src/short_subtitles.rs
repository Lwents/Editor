use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ShortSubtitleOptions {
    pub text: String,
    pub start_time: f64,
    pub duration: f64,
    pub word_widths: Vec<f64>,
    pub max_width: f64,
    pub max_words: usize,
}
#[derive(Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct ShortSubtitle {
    pub text: String,
    pub start_time: f64,
    pub duration: f64,
}

// Layout measurements come from the UI; segmentation and proportional timing are shared.
pub fn short_subtitles(o: ShortSubtitleOptions) -> Vec<ShortSubtitle> {
    let words: Vec<&str> = o.text.split_whitespace().collect();
    if words.is_empty() || !o.duration.is_finite() || o.duration <= 0.0 || !o.start_time.is_finite()
    {
        return vec![];
    }
    let limit = o.max_words.clamp(2, 12);
    let mut groups: Vec<Vec<usize>> = vec![];
    let mut group = vec![];
    let mut width = 0.0;
    for (i, word) in words.iter().enumerate() {
        let w = o.word_widths.get(i).copied().unwrap_or(0.0).max(0.0);
        if !group.is_empty() && (group.len() >= limit || width + w > o.max_width) {
            groups.push(std::mem::take(&mut group));
            width = 0.0;
        }
        group.push(i);
        width += w;
        if group.len() >= 3 && word.ends_with([',', '.', '!', '?', ';', ':']) {
            groups.push(std::mem::take(&mut group));
            width = 0.0;
        }
    }
    if !group.is_empty() {
        groups.push(group);
    }
    // Avoid leaving just one word on the final cue when another word will fit.
    if groups.len() > 1 && groups.last().unwrap().len() == 1 {
        let last = groups.len() - 1;
        if groups[last - 1].len() > 2 {
            let moved = *groups[last - 1].last().unwrap();
            let current = groups[last][0];
            if o.word_widths.get(moved).copied().unwrap_or(0.0)
                + o.word_widths.get(current).copied().unwrap_or(0.0)
                <= o.max_width
            {
                groups[last - 1].pop();
                groups[last].insert(0, moved);
            }
        }
    }
    let weight = |g: &[usize]| {
        g.iter()
            .map(|&i| words[i].chars().count().max(1))
            .sum::<usize>() as f64
    };
    let total: f64 = groups.iter().map(|g| weight(g)).sum();
    let mut elapsed = 0.0;
    let count = groups.len();
    groups
        .into_iter()
        .enumerate()
        .map(|(i, g)| {
            let start = o.start_time + elapsed;
            let duration = if i + 1 == count {
                o.duration - elapsed
            } else {
                o.duration * weight(&g) / total
            };
            elapsed += duration;
            ShortSubtitle {
                text: g.iter().map(|&i| words[i]).collect::<Vec<_>>().join(" "),
                start_time: start,
                duration,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn retains_words_and_original_interval() {
        let text = "Hít sâu một hơi, ngậm chặt môi để giữ không khí lại trong miệng,";
        let n = text.split_whitespace().count();
        let cues = short_subtitles(ShortSubtitleOptions {
            text: text.into(),
            start_time: 4.0,
            duration: 2.87,
            word_widths: vec![35.0; n],
            max_width: 210.0,
            max_words: 7,
        });
        assert!(cues.len() >= 3);
        assert_eq!(
            cues.iter()
                .map(|c| c.text.as_str())
                .collect::<Vec<_>>()
                .join(" "),
            text
        );
        assert_eq!(cues[0].start_time, 4.0);
        assert!(
            (cues.last().unwrap().start_time + cues.last().unwrap().duration - 6.87).abs() < 1e-9
        );
        for pair in cues.windows(2) {
            assert!((pair[0].start_time + pair[0].duration - pair[1].start_time).abs() < 1e-9);
        }
        assert!(
            cues.iter()
                .all(|c| c.duration > 0.0 && c.text.split_whitespace().count() <= 7)
        );
    }
}
