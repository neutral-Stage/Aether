import Foundation

/// Quick repeated presses of one key: `register(at:)` is true on the `count`-th press
/// when each press came within `gap` seconds of the one before. Any other key in
/// between should call `reset()`.
struct TapSequence {
    let count: Int
    let gap: TimeInterval
    private(set) var times: [TimeInterval] = []

    init(count: Int, gap: TimeInterval) {
        self.count = count
        self.gap = gap
    }

    mutating func register(at time: TimeInterval) -> Bool {
        if let last = times.last, time - last > gap || time < last {
            times.removeAll()
        }
        times.append(time)
        if times.count >= count {
            times.removeAll()
            return true
        }
        return false
    }

    mutating func reset() {
        times.removeAll()
    }
}
