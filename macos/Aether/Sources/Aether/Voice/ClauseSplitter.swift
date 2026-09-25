import Foundation

/// Splits streamed text into speakable clauses, so speech can start at the first one
/// instead of after the whole answer.
///
/// A clause ends at a sentence end (. ! ? … followed by a space), at a line break, or,
/// once it is long enough, at a comma, semicolon or colon followed by a space.
struct ClauseSplitter {
    private(set) var buffer = ""
    /// Shortest clause that may end at a comma (short comma pieces sound choppy).
    var minCommaClause = 48

    /// Adds streamed text; returns the clauses that are now complete.
    mutating func feed(_ text: String) -> [String] {
        buffer += text
        var out: [String] = []
        while let cut = nextCut() {
            let clause = String(buffer[..<cut]).trimmingCharacters(in: .whitespacesAndNewlines)
            buffer = String(buffer[cut...])
            if !clause.isEmpty { out.append(clause) }
        }
        return out
    }

    /// The end of the stream: whatever is left, if anything.
    mutating func flush() -> String? {
        let rest = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
        buffer = ""
        return rest.isEmpty ? nil : rest
    }

    private func nextCut() -> String.Index? {
        var count = 0
        var idx = buffer.startIndex
        while idx < buffer.endIndex {
            let ch = buffer[idx]
            let next = buffer.index(after: idx)
            count += 1
            if ch == "\n" { return next }
            let followedBySpace = next < buffer.endIndex && buffer[next].isWhitespace
            if followedBySpace, "!?…".contains(ch) || (ch == "." && count >= 3) {
                return next
            }
            if followedBySpace, ",;:".contains(ch), count >= minCommaClause {
                return next
            }
            idx = next
        }
        return nil
    }
}
