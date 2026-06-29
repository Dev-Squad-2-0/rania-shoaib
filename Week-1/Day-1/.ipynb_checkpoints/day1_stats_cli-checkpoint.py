
def mean(nums):
    return sum(nums) / len(nums)

def median(nums):
    sorted_nums = sorted(nums)
    n = len(sorted_nums)
    if n % 2 == 1:
        return sorted_nums[n // 2]
    else:
        m1 = sorted_nums[n // 2 -1]
        m2 = sorted_nums[n // 2]
        return (m1 + m2) / 2
    
def mode(nums):
    frequency = {}
    for num in nums:
        frequency[num] = nums.count(num)
    max_freq = max(frequency.values())
    modes = []
    for num, freq in frequency.items():
        if freq == max_freq:
            modes.append(num)
    if len(modes) == len(set(nums)):
        return "no unique mode"
    else:
        return modes
    
def display_stats(nums):
    print("Mean:", mean(nums))
    print("Median:", median(nums))
    print("Mode:", mode(nums))
    print("Min:", min(nums))
    print("Max:", max(nums))

def main():
    user_input = input("Enter numbers separated by spaces: ").strip()
    try:
        nums = [float(x) for x in user_input.split()]
        if nums:
            display_stats(nums)
    except ValueError:
        print("Error, all inputs must be numbers.")
   

if __name__ == "__main__":
     main()



        
