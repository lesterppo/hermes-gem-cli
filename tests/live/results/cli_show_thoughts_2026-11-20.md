I’d love to help you with this! Proving the time complexity of binary search is one of the best ways to build strong algorithmic intuition, and pairing the proof with clean code makes it super easy to see how the math maps to execution.

---

### Step-by-Step Proof of $O(\log n)$ Time Complexity

Suppose we have a sorted list of $n$ elements (where $n \ge 1$), and we are searching for a target value using binary search.

#### Step 1: Model the Problem Size After Each Comparison
At each step of binary search, we inspect the **middle element** of the current search range:
* If it matches the target, we stop early (best case: $O(1)$).
* Otherwise, because the list is sorted, we eliminate **half** of the remaining elements (either the left half or the right half, plus the middle element itself).

Let $n_k$ represent the maximum number of elements remaining after $k$ comparisons (in the worst case, where the element is not found or is found at the very end):
* **After 0 comparisons (start):** $n_0 = n$
* **After 1 comparison:** $n_1 \le \frac{n}{2}$
* **After 2 comparisons:** $n_2 \le \frac{n}{4} = \frac{n}{2^2}$
* **After $k$ comparisons:** $n_k \le \frac{n}{2^k}$

#### Step 2: Determine the Stopping Condition
The search stops when there are no elements left to check—that is, when the remaining subarray size drops to **1** (or **0**, depending on boundary conventions). Setting the worst-case remaining size to $1$:

$$\frac{n}{2^k} = 1$$

#### Step 3: Solve for $k$ (the Number of Steps)
Multiply both sides by $2^k$:

$$n = 2^k$$

Take the base-2 logarithm ($\log_2$) of both sides:

$$\log_2(n) = \log_2(2^k)$$

$$k = \log_2(n)$$

#### Step 4: Translate to Big-$O$ Notation
Since each loop iteration / comparison takes constant time, $O(1)$, the total worst-case running time $T(n)$ is proportional to the number of iterations $k$:

$$T(n) = c \cdot \log_2(n) + d$$

In Big-$O$ notation, we drop constant multipliers and lower-order terms, and because all logarithms are related by a constant change-of-base factor ($\log_a n = \frac{\log_b n}{\log_b a}$), the base is omitted:

$$T(n) = O(\log n)$$

#### Assumptions & Edge Cases to Keep in Mind
* **Sorted input:** The list must be sorted (ascending or descending); otherwise, eliminating half the list is invalid.
* **Random access ($O(1)$ indexing):** Standard Python `list` supports $O(1)$ index access (`arr[mid]`), which keeps each step constant time. (On a linked list, finding the middle takes $O(n)$ time, breaking the $O(\log n)$ guarantee.)
* **Integer overflow in other languages:** In languages with fixed-width integers (like C++ or Java), `mid = (low + high) // 2` can overflow; using `low + (high - low) // 2` avoids this. Python integers have arbitrary precision, though `low + (high - low) // 2` is still a great habit.

---

### Python Implementation & How to Use It

Here is an iterative implementation of binary search with comments mapping directly to the proof steps above.

```python
from typing import List, Optional


def binary_search(sorted_list: List[int], target: int) -> Optional[int]:
    """
    Perform binary search on a sorted list to find `target`.

    Returns the index of `target` if found, otherwise `None`.
    Worst-case time complexity: O(log n), where n = len(sorted_list).
    Space complexity: O(1) auxiliary space (iterative).
    """
    # Step 1 (Start): Full list of size n is our initial search space.
    # `left` and `right` track the inclusive bounds of the active subarray.
    left: int = 0
    right: int = len(sorted_list) - 1

    # Step 2 (Stopping condition): Loop continues while at least 1 element
    # remains in [left, right]. Each iteration cuts the range by ~half (n / 2^k).
    while left <= right:
        # Pick the middle index; each step does O(1) work before halving.
        mid: int = left + (right - left) // 2
        mid_value: int = sorted_list[mid]

        if mid_value == target:
            # Best/early exit: target found.
            return mid
        elif mid_value < target:
            # Step 1 (Halving): Target must be in the right half; discard left half + mid.
            left = mid + 1
        else:
            # Step 1 (Halving): Target must be in the left half; discard right half + mid.
            right = mid - 1

    # Step 3 & 4: After at most ~log2(n) steps, search space is empty -> O(log n).
    return None


# --- Example Usage ---
if __name__ == "__main__":
    numbers = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91]

    found_index = binary_search(numbers, 23)
    print(f"Index of 23: {found_index}")  # Expected: 5

    missing_index = binary_search(numbers, 40)
    print(f"Index of 40: {missing_index}")  # Expected: None
```

#### Importing & Adapting the Code
* **Same project / module:** Save the function in a file such as `search.py` and import it with `from search import binary_search`.
* **Adjustable parameters / customization:**
  * `sorted_list`: Pass any sorted sequence supporting `len()` and index comparison (`<`, `==`). If you need custom objects or descending order, you can adapt the comparison logic or pass a `key` function.
  * `target`: The value you are searching for.
  * Return value: Currently returns the `0`-based index or `None`; you can change `return None` to `return -1` if that fits your codebase conventions better.

If you're planning to use this in a specific project, language, or data structure—or want to walk through a recurrence relation proof like $T(n) = T(n/2) + O(1)$—let me know what you're building!