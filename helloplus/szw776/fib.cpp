// Print the first n terms of the Fibonacci sequence.
#include <iostream>

int main() {
	int n = 10;

	unsigned long long a = 0;
	unsigned long long b = 1;

	for (int i = 0; i < n; ++i) {
		if (i > 0) {
			std::cout << ' ';
		}
		std::cout << a;
		unsigned long long next = a + b;
		a = b;
		b = next;
	}

	std::cout << '\n';
	return 0;
}
