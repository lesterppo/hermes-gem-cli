We can prove $\sqrt{2}$ is irrational using a **proof by contradiction**:

1. **Assume the opposite:** Suppose $\sqrt{2}$ is rational. By definition, it can be written as a fully simplified fraction of two integers $a$ and $b$:
   $$\sqrt{2} = \frac{a}{b}$$
   where $b \neq 0$, and $a$ and $b$ have **no common factors** other than $1$ (meaning they cannot both be even).

2. **Square both sides:**
   $$2 = \frac{a^2}{b^2} \implies a^2 = 2b^2$$

3. **Deduce that $a$ is even:** Since $a^2$ equals $2$ times an integer ($b^2$), $a^2$ is even. Because the square of an odd number is always odd, $a$ itself must also be even. Therefore, we can write $a = 2k$ for some integer $k$.

4. **Substitute and deduce that $b$ is even:** Plug $a = 2k$ back into the equation:
   $$(2k)^2 = 2b^2 \implies 4k^2 = 2b^2 \implies b^2 = 2k^2$$
   By the same logic, $b^2$ is even, which means $b$ must also be even.

5. **Contradiction:** We have shown that both $a$ and $b$ are even, meaning they share a common factor of $2$. This contradicts our initial assumption that $\frac{a}{b}$ was fully simplified.

Because the assumption leads to an impossibility, $\sqrt{2}$ cannot be rational—therefore, **$\sqrt{2}$ is irrational**.