/// Keep room for transitions at both ends of a clip without overlapping them.
pub fn transition_duration(requested: i64, from_duration: i64, to_duration: i64) -> i64 {
    requested
        .max(0)
        .min(from_duration.max(0) / 2)
        .min(to_duration.max(0) / 2)
}
/// -1 means the playhead is outside the incoming clip's transition interval.
pub fn transition_progress(local_time: i64, duration: i64) -> f64 {
    if duration <= 0 || local_time < 0 || local_time >= duration {
        -1.0
    } else {
        local_time as f64 / duration as f64
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn clamps_overlap_and_rejects_invalid_clips() {
        assert_eq!(transition_duration(60, 100, 80), 40);
        assert_eq!(transition_duration(5, 100, 80), 5);
        assert_eq!(transition_duration(5, -1, 80), 0);
        assert_eq!(transition_duration(-5, 100, 80), 0);
    }
    #[test]
    fn progress_has_exact_boundaries() {
        assert_eq!(transition_progress(-1, 100), -1.0);
        assert_eq!(transition_progress(0, 100), 0.0);
        assert_eq!(transition_progress(50, 100), 0.5);
        assert_eq!(transition_progress(100, 100), -1.0);
        assert_eq!(transition_progress(0, 0), -1.0);
    }
}
