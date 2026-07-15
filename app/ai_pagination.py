"""
Generation Pagination Service — ported from IntelliSchool for NhavaLearn.
Ensures ALL requested questions/content is generated without truncation.
Implements intelligent continuation and pagination.

Unchanged from IntelliSchool's app/generation_pagination.py: this module has
no FastAPI/SQLAlchemy dependency, so it ports as-is. Called by ai_generator.py.
"""

import logging
import re
from typing import Dict, List, Tuple, Optional
import time

logger = logging.getLogger("IntelliSchool")


# =============================================================================
# GENERATION TRACKING & COUNTING
# =============================================================================

def count_questions_in_text(text: str) -> int:
    """
    Count number of questions in generated text
    Supports multiple formats:
    - Question 1: ...
    - 1. ...
    - Q1: ...
    - 1) ...
    """
    patterns = [
        r'Question\s+\d+[:\.]',   # Question 1: or Question 1.
        r'\*\*Question\s+\d+',   # **Question 1 (markdown bold)
        r'Q\d+[:\.)\.]',          # Q1: or Q1. or Q1)
        r'^\d+[\.]',               # 1. at line start
        r'^\d+\)',                  # 1) at line start
        r'^\*\*\d+[\.]',         # **1. (bold numbered)
        r'###\s+Question\s+\d+',  # ### Question 1 (markdown header)
        r'^#+\s+\d+[\.]',         # ## 1. (numbered header)
    ]
    
    count = 0
    for pattern in patterns:
        matches = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)
        if matches:
            count = max(count, len(matches))
    
    return count


def extract_last_question_number(text: str) -> Optional[int]:
    """Extract the last question number from text"""
    patterns = [
        r'Question\s+(\d+):',
        r'Q(\d+):',
        r'^(\d+)\.',
        r'^(\d+)\)',
    ]
    
    last_num = None
    for pattern in patterns:
        matches = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)
        if matches:
            try:
                numbers = [int(m) for m in matches]
                last_num = max(numbers) if numbers else last_num
            except:
                pass
    
    return last_num


def is_generation_complete(text: str, requested_count: int) -> bool:
    """
    Check if generation is complete
    
    Args:
        text: Generated text
        requested_count: Number of items requested
    
    Returns:
        True if complete, False if truncated
    """
    actual_count = count_questions_in_text(text)
    
    # Allow small deviation (sometimes formatting causes off-by-one)
    tolerance = 1
    
    is_complete = actual_count >= (requested_count - tolerance)
    
    logger.info(f"Generation check: {actual_count}/{requested_count} items (Complete: {is_complete})")
    
    return is_complete


# =============================================================================
# CONTINUATION GENERATION
# =============================================================================

def build_continuation_prompt(
    original_prompt: str,
    partial_content: str,
    requested_count: int,
    current_count: int,
    content_type: str = "questions"
) -> str:
    """
    Build prompt for continuing incomplete generation
    
    Args:
        original_prompt: Original generation request
        partial_content: What was generated so far
        requested_count: Total items requested
        current_count: Items generated so far
        content_type: Type of content (questions, flashcards, etc.)
    
    Returns:
        Continuation prompt
    """
    remaining = requested_count - current_count
    
    # Extract last few items for context
    lines = partial_content.strip().split('\n')
    context_lines = lines[-20:] if len(lines) > 20 else lines  # Last 20 lines as context
    context = '\n'.join(context_lines)
    
    continuation_prompt = f"""CONTINUATION REQUEST - DO NOT REPEAT PREVIOUS CONTENT

You previously generated {current_count} {content_type}. You need to generate {remaining} MORE {content_type} to complete the request.

START from {content_type.capitalize()} {current_count + 1} and continue until {content_type.capitalize()} {requested_count}.

Previous {content_type} (for context - DO NOT repeat these):
---
{context}
---

Original request:
{original_prompt}

Generate {remaining} MORE {content_type} starting from number {current_count + 1}.

Begin NOW:"""
    
    return continuation_prompt


def generate_with_pagination(
    generate_function,
    user_prompt: str,
    system_prompt: str,
    max_tokens: int,
    requested_count: int,
    content_type: str = "questions",
    max_attempts: int = 3
) -> Tuple[str, Dict]:
    """
    Generate content with automatic continuation if truncated
    
    Args:
        generate_function: Function to call for generation (e.g., smart_generate)
        user_prompt: User's prompt
        system_prompt: System prompt
        max_tokens: Max tokens per generation
        requested_count: Number of items requested
        content_type: Type (questions, flashcards, etc.)
        max_attempts: Max continuation attempts
    
    Returns:
        (complete_content, metadata)
    """
    all_content = []
    total_count = 0
    attempts = 0
    last_error = None

    logger.info(f"Starting paginated generation: {requested_count} {content_type}")
    
    # First generation
    current_prompt = user_prompt
    
    while total_count < requested_count and attempts < max_attempts:
        attempts += 1
        
        logger.info(f"Generation attempt {attempts}: {total_count}/{requested_count} {content_type}")
        
        # Generate content
        try:
            content = generate_function(
                current_prompt,
                system_prompt,
                max_tokens
            )
        except Exception as e:
            logger.error(f"Generation error on attempt {attempts}: {e}")
            last_error = str(e)
            break
        
        # Count what we got
        current_count = count_questions_in_text(content)
        
        if current_count == 0:
            logger.warning(f"No {content_type} detected in output. Breaking.")
            all_content.append(content)
            break
        
        # Add to collection
        all_content.append(content)
        total_count += current_count
        
        logger.info(f"Attempt {attempts} generated {current_count} {content_type}. Total: {total_count}/{requested_count}")
        
        # Check if complete
        if total_count >= requested_count:
            logger.info(f"✓ Generation complete: {total_count}/{requested_count}")
            break
        
        # Need continuation
        remaining = requested_count - total_count
        logger.info(f"⚠ Incomplete generation. Need {remaining} more {content_type}. Continuing...")
        
        # Build continuation prompt
        current_prompt = build_continuation_prompt(
            user_prompt,
            content,
            requested_count,
            total_count,
            content_type
        )
        
        # Small delay to avoid rate limiting
        time.sleep(0.5)
    
    # Combine all content
    final_content = "\n\n".join(all_content)
    
    # Metadata
    metadata = {
        'requested': requested_count,
        'generated': total_count,
        'attempts': attempts,
        'complete': total_count >= requested_count,
        'success_rate': (total_count / requested_count) * 100 if requested_count > 0 else 0,
        'error': last_error if total_count == 0 else None,
    }
    
    logger.info(f"Final result: {total_count}/{requested_count} {content_type} in {attempts} attempts")
    
    return final_content, metadata


# =============================================================================
# SMART TOKEN ALLOCATION
# =============================================================================

def calculate_optimal_tokens(requested_count: int, content_type: str) -> int:
    """
    Calculate optimal max_tokens based on request
    
    Args:
        requested_count: Number of items requested
        content_type: Type of content
    
    Returns:
        Recommended max_tokens
    """
    # Average tokens per item type
    tokens_per_item = {
        'questions': 150,      # Exam questions with options
        'flashcards': 80,      # Front + back of flashcard
        'quiz': 120,           # Quiz questions
        'worksheet': 200,      # Worksheet problems
        'study_guide': 250,    # Study guide sections
    }
    
    base_tokens = tokens_per_item.get(content_type, 150)
    
    # Add overhead for formatting, instructions, etc.
    overhead = 300
    
    # Calculate total
    optimal = (base_tokens * requested_count) + overhead
    
    # Cap at reasonable limits
    optimal = min(optimal, 4000)  # Don't exceed model limits
    optimal = max(optimal, 800)   # Minimum reasonable amount
    
    logger.info(f"Optimal tokens for {requested_count} {content_type}: {optimal}")
    
    return optimal


# =============================================================================
# ENHANCED GENERATION FUNCTIONS
# =============================================================================

def generate_exam_with_pagination(
    generate_function,
    user_prompt: str,
    system_prompt: str,
    num_questions: int,
    question_type: str = "multiple_choice"
) -> Tuple[str, Dict]:
    """
    Generate exam questions with automatic pagination.
    Allocates tokens based on question type — essay/short-answer need more than multiple-choice.

    Returns:
        (questions, metadata)
    """
    # Token budgets per question type (tokens per question)
    tokens_per_question = {
        "multiple_choice": 150,
        "true_false":      80,
        "short_answer":    350,
        "essay":           500,
        "mixed":           350,
        "case_study":      450,
        "comprehension":   400,
    }
    per_q = tokens_per_question.get(question_type, 300)
    overhead = 400
    optimal_tokens = min((per_q * num_questions) + overhead, 6000)
    optimal_tokens = max(optimal_tokens, 1000)

    logger.info(f"Exam tokens for {num_questions} x {question_type}: {optimal_tokens}")

    return generate_with_pagination(
        generate_function,
        user_prompt,
        system_prompt,
        optimal_tokens,
        num_questions,
        content_type="questions",
        max_attempts=3
    )


def generate_quiz_with_pagination(
    generate_function,
    user_prompt: str,
    system_prompt: str,
    num_questions: int
) -> Tuple[str, Dict]:
    """Generate quiz with pagination"""
    optimal_tokens = calculate_optimal_tokens(num_questions, 'quiz')
    
    return generate_with_pagination(
        generate_function,
        user_prompt,
        system_prompt,
        optimal_tokens,
        num_questions,
        content_type="questions",
        max_attempts=3
    )


def generate_flashcards_with_pagination(
    generate_function,
    user_prompt: str,
    system_prompt: str,
    num_cards: int
) -> Tuple[str, Dict]:
    """Generate flashcards with pagination"""
    optimal_tokens = calculate_optimal_tokens(num_cards, 'flashcards')
    
    return generate_with_pagination(
        generate_function,
        user_prompt,
        system_prompt,
        optimal_tokens,
        num_cards,
        content_type="flashcards",
        max_attempts=3
    )


# =============================================================================
# CONTENT FORMATTING & CLEANUP
# =============================================================================

def remove_duplicate_questions(text: str) -> str:
    """
    Remove duplicate questions from combined content
    Keeps first occurrence of each question
    """
    lines = text.split('\n')
    seen_questions = set()
    cleaned_lines = []
    
    current_question = []
    question_started = False
    
    for line in lines:
        # Check if this is a new question
        if re.match(r'(Question\s+\d+:|Q\d+:|\d+\.|\d+\))', line, re.IGNORECASE):
            # Save previous question if any
            if current_question:
                q_text = '\n'.join(current_question)
                q_hash = hash(q_text[:100])  # Hash first 100 chars
                
                if q_hash not in seen_questions:
                    seen_questions.add(q_hash)
                    cleaned_lines.extend(current_question)
            
            # Start new question
            current_question = [line]
            question_started = True
        else:
            if question_started:
                current_question.append(line)
    
    # Add last question
    if current_question:
        q_text = '\n'.join(current_question)
        q_hash = hash(q_text[:100])
        if q_hash not in seen_questions:
            cleaned_lines.extend(current_question)
    
    return '\n'.join(cleaned_lines)


def renumber_questions(text: str, start_num: int = 1) -> str:
    """
    Renumber all questions sequentially
    Useful after combining multiple generations
    """
    lines = text.split('\n')
    renumbered = []
    current_num = start_num
    
    for line in lines:
        # Match question patterns and replace with sequential number
        if re.match(r'Question\s+\d+:', line, re.IGNORECASE):
            line = re.sub(r'Question\s+\d+:', f'Question {current_num}:', line, flags=re.IGNORECASE)
            current_num += 1
        elif re.match(r'Q\d+:', line, re.IGNORECASE):
            line = re.sub(r'Q\d+:', f'Q{current_num}:', line, flags=re.IGNORECASE)
            current_num += 1
        elif re.match(r'^\d+\.', line):
            line = re.sub(r'^\d+\.', f'{current_num}.', line)
            current_num += 1
        elif re.match(r'^\d+\)', line):
            line = re.sub(r'^\d+\)', f'{current_num})', line)
            current_num += 1
        
        renumbered.append(line)
    
    return '\n'.join(renumbered)


def format_paginated_content(content: str, metadata: Dict) -> str:
    """
    Format paginated content with cleanup
    
    Args:
        content: Combined content from multiple generations
        metadata: Generation metadata
    
    Returns:
        Cleaned and formatted content
    """
    # Remove duplicates
    content = remove_duplicate_questions(content)
    
    # Renumber sequentially
    content = renumber_questions(content, start_num=1)
    
    # Add generation info footer (optional)
    if not metadata.get('complete'):
        footer = f"\n\n---\n[Generated {metadata['generated']}/{metadata['requested']} items in {metadata['attempts']} attempts]\n"
        content += footer
    
    return content


# =============================================================================
# PROGRESS TRACKING FOR FRONTEND
# =============================================================================

class GenerationProgress:
    """Track generation progress for real-time updates"""
    
    def __init__(self, total: int):
        self.total = total
        self.current = 0
        self.status = "starting"
        self.attempts = 0
    
    def update(self, current: int, status: str):
        self.current = current
        self.status = status
    
    def increment_attempt(self):
        self.attempts += 1
    
    def get_progress(self) -> Dict:
        return {
            'current': self.current,
            'total': self.total,
            'percentage': (self.current / self.total * 100) if self.total > 0 else 0,
            'status': self.status,
            'attempts': self.attempts
        }


# =============================================================================
# STREAMING PAGINATION (FOR REAL-TIME UPDATES)
# =============================================================================

async def generate_with_pagination_streaming(
    generate_function,
    user_prompt: str,
    system_prompt: str,
    max_tokens: int,
    requested_count: int,
    content_type: str = "questions"
):
    """
    Generate with pagination and yield progress updates
    For use with streaming endpoints
    
    Yields:
        Progress updates and final content
    """
    progress = GenerationProgress(requested_count)
    all_content = []
    total_count = 0
    
    yield f"data: {{'status': 'starting', 'progress': 0}}\n\n"
    
    current_prompt = user_prompt
    max_attempts = 3
    
    for attempt in range(1, max_attempts + 1):
        progress.increment_attempt()
        
        yield f"data: {{'status': 'generating', 'attempt': {attempt}, 'progress': {progress.get_progress()['percentage']:.0f}}}\n\n"
        
        try:
            content = generate_function(current_prompt, system_prompt, max_tokens)
        except Exception as e:
            yield f"data: {{'status': 'error', 'message': '{str(e)}'}}\n\n"
            break
        
        current_count = count_questions_in_text(content)
        all_content.append(content)
        total_count += current_count
        
        progress.update(total_count, f"generated_{total_count}")
        
        yield f"data: {{'status': 'progress', 'current': {total_count}, 'total': {requested_count}, 'progress': {progress.get_progress()['percentage']:.0f}}}\n\n"
        
        if total_count >= requested_count:
            break
        
        # Need continuation
        current_prompt = build_continuation_prompt(
            user_prompt, content, requested_count, total_count, content_type
        )
    
    # Format final content
    final_content = "\n\n".join(all_content)
    final_content = format_paginated_content(final_content, {
        'requested': requested_count,
        'generated': total_count,
        'attempts': attempt,
        'complete': total_count >= requested_count
    })
    
    yield f"data: {{'status': 'complete', 'content': {repr(final_content)}, 'generated': {total_count}}}\n\n"
    yield "data: [DONE]\n\n"


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'generate_with_pagination',
    'generate_exam_with_pagination',
    'generate_quiz_with_pagination',
    'generate_flashcards_with_pagination',
    'count_questions_in_text',
    'is_generation_complete',
    'calculate_optimal_tokens',
    'format_paginated_content',
    'GenerationProgress',
    'generate_with_pagination_streaming'
]
